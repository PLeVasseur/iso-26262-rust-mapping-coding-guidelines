#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import yaml

FORBIDDEN_SQL_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|attach|detach|vacuum|create|reindex|"
    r"replace|truncate|pragma)\b",
    re.IGNORECASE,
)


class GuardrailError(RuntimeError):
    """Raised when a query violates the queryability guardrails."""


@dataclass(frozen=True)
class QuerySpec:
    query_id: str
    sql: str
    params: tuple[str, ...]
    row_limit: int
    requires_order: bool


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _load_contract_payload(contract_path: Path) -> dict[str, Any]:
    with contract_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise GuardrailError("Contract payload must be a mapping")
    return payload


def _extract_query_specs(payload: dict[str, Any]) -> tuple[dict[str, QuerySpec], int]:
    defaults = payload.get("defaults") or {}
    default_row_limit = int(defaults.get("row_limit", 200))
    if default_row_limit <= 0:
        raise GuardrailError("Contract default row_limit must be positive")

    raw_queries = payload.get("queries") or {}
    if not isinstance(raw_queries, dict) or not raw_queries:
        raise GuardrailError("Contract must define at least one query")

    specs: dict[str, QuerySpec] = {}
    for query_id, raw_spec in raw_queries.items():
        if not isinstance(query_id, str) or not query_id:
            raise GuardrailError("Query id must be a non-empty string")
        if not isinstance(raw_spec, dict):
            raise GuardrailError(f"Query spec for {query_id} must be a mapping")

        sql = str(raw_spec.get("sql", "")).strip()
        if not sql:
            raise GuardrailError(f"Query {query_id} must provide SQL")

        raw_params = raw_spec.get("params") or []
        if not isinstance(raw_params, list):
            raise GuardrailError(f"Query {query_id} params must be a list")
        params: list[str] = []
        for value in raw_params:
            if not isinstance(value, str) or not value:
                raise GuardrailError(f"Query {query_id} has invalid param name")
            params.append(value)

        row_limit = int(raw_spec.get("row_limit", default_row_limit))
        if row_limit <= 0:
            raise GuardrailError(f"Query {query_id} row_limit must be positive")

        requires_order = bool(raw_spec.get("requires_order", False))
        specs[query_id] = QuerySpec(
            query_id=query_id,
            sql=sql,
            params=tuple(params),
            row_limit=row_limit,
            requires_order=requires_order,
        )

    return specs, default_row_limit


def _validate_sql(sql: str, requires_order: bool) -> None:
    normalized = sql.strip().rstrip(";")
    if FORBIDDEN_SQL_RE.search(normalized):
        raise GuardrailError("Query SQL contains forbidden write/DDL keyword")

    lowered = " ".join(normalized.lower().split())
    if requires_order and " order by " not in f" {lowered} ":
        raise GuardrailError("Query requires deterministic ORDER BY clause")


def _validate_params(expected: tuple[str, ...], provided: dict[str, Any]) -> None:
    expected_set = set(expected)
    provided_set = set(provided.keys())
    missing = sorted(expected_set - provided_set)
    extra = sorted(provided_set - expected_set)
    if missing:
        raise GuardrailError(f"Missing required query params: {', '.join(missing)}")
    if extra:
        raise GuardrailError(f"Unexpected query params: {', '.join(extra)}")


def _write_query_log(query_log_root: Path, payload: dict[str, Any]) -> None:
    query_log_root.mkdir(parents=True, exist_ok=True)
    filename = datetime.now(UTC).strftime("%Y%m%d.jsonl")
    path = query_log_root / filename
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True))
        handle.write("\n")


def execute_contract_query(
    db_path: Path,
    contract_path: Path,
    query_id: str,
    params: dict[str, Any] | None = None,
    row_limit: int | None = None,
    query_log_root: Path | None = None,
) -> dict[str, Any]:
    params = params or {}
    payload = _load_contract_payload(contract_path)
    specs, default_row_limit = _extract_query_specs(payload)

    if query_id not in specs:
        raise GuardrailError(f"Unknown query id: {query_id}")
    spec = specs[query_id]

    _validate_sql(spec.sql, spec.requires_order)
    _validate_params(spec.params, params)

    effective_limit = row_limit if row_limit is not None else spec.row_limit
    if effective_limit <= 0:
        raise GuardrailError("Row limit must be positive")
    if effective_limit > 5000:
        raise GuardrailError("Row limit exceeds guardrail maximum (5000)")

    sanitized_sql = spec.sql.strip().rstrip(";")
    lowered = " ".join(sanitized_sql.lower().split())
    if " limit " in f" {lowered} ":
        wrapped_sql = sanitized_sql
    else:
        wrapped_sql = f"{sanitized_sql} LIMIT :__row_limit"
    bound_params = dict(params)
    if ":__row_limit" in wrapped_sql:
        bound_params["__row_limit"] = int(effective_limit)

    started = perf_counter()
    db_uri = f"file:{db_path}?mode=ro"
    connection = sqlite3.connect(db_uri, uri=True, timeout=2.0)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 1500")
        rows = connection.execute(wrapped_sql, bound_params).fetchall()
    finally:
        connection.close()
    duration_ms = (perf_counter() - started) * 1000.0

    materialized_rows = [dict(row) for row in rows]
    result = {
        "query_id": query_id,
        "row_count": len(materialized_rows),
        "duration_ms": round(duration_ms, 3),
        "rows": materialized_rows,
    }

    if query_log_root is not None:
        _write_query_log(
            query_log_root,
            {
                "timestamp": _utc_now(),
                "db_path": str(db_path),
                "query_id": query_id,
                "param_keys": sorted(params.keys()),
                "row_count": result["row_count"],
                "duration_ms": result["duration_ms"],
            },
        )

    _ = default_row_limit  # Keep for future diagnostics.
    return result
