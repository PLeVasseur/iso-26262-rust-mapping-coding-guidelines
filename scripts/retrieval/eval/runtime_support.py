from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"YAML payload at {path} must be a mapping")
    return payload


def validate_required_evidence_fields(db_path: Path, prompts: list[dict[str, Any]]) -> None:
    required_fields = sorted(
        {
            str(field).strip()
            for prompt in prompts
            for field in list(prompt.get("required_evidence_fields") or [])
            if str(field).strip()
        }
    )
    if not required_fields:
        return

    known_field_sources: dict[str, tuple[str, str]] = {
        "item_path": ("core_docs_chunk_metadata", "item_path"),
        "item_kind": ("core_docs_chunk_metadata", "item_kind"),
        "signature": ("core_docs_chunk_metadata", "signature"),
        "stability": ("core_docs_chunk_metadata", "stability"),
        "safety_notes": ("core_docs_chunk_metadata", "safety_notes"),
        "panic_behavior": ("core_docs_chunk_metadata", "panic_behavior"),
        "example_snippets": ("core_docs_chunk_metadata", "example_snippets"),
        "target_triple": ("core_docs_chunk_metadata", "target_triple"),
        "target_env": ("core_docs_chunk_metadata", "target_env"),
        "cfg_signature": ("core_docs_chunk_metadata", "cfg_signature"),
        "cfg_signature_sha256": ("core_docs_chunk_metadata", "cfg_signature_sha256"),
        "row_markers": ("table1_rows", "row_marker"),
    }

    missing_mappings = [field for field in required_fields if field not in known_field_sources]
    if missing_mappings:
        raise RuntimeError(
            "Unknown required_evidence_fields in prompt suite: " + ", ".join(missing_mappings)
        )

    connection = sqlite3.connect(db_path)
    try:
        for field in required_fields:
            table_name, column_name = known_field_sources[field]
            columns = [
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
            ]
            if column_name not in columns:
                raise RuntimeError(
                    f"Required evidence field {field} missing column {column_name} in {table_name}"
                )
            sql = (
                f"SELECT {column_name} FROM {table_name} "
                f"WHERE {column_name} IS NOT NULL AND {column_name} <> '' LIMIT 1"
            )
            value = connection.execute(sql).fetchone()
            if value is None:
                raise RuntimeError(
                    "Required evidence field "
                    f"{field} has no non-empty values in {table_name}.{column_name}"
                )
    finally:
        connection.close()


def is_relevant(row: dict[str, Any], prompt: dict[str, Any]) -> bool:
    statement_id = str(row.get("statement_id", ""))
    source_anchor = str(row.get("source_anchor", ""))
    row_markers = {value.lower() for value in row.get("row_markers", [])}
    statement_text = str(row.get("statement_text", "")).lower()

    if statement_id and statement_id in set(prompt["relevant_statement_ids"]):
        return True

    anchor_prefixes = [prefix for prefix in prompt["relevant_anchor_prefixes"] if prefix]
    if anchor_prefixes and any(source_anchor.startswith(prefix) for prefix in anchor_prefixes):
        return True

    expected_rows = set(prompt["expected_row_markers"])
    row_match = bool(expected_rows.intersection(row_markers)) if expected_rows else True

    relevant_terms = [term for term in prompt["relevant_terms"] if term]
    if relevant_terms:
        term_match = any(term in statement_text for term in relevant_terms)
        if row_match and term_match:
            return True

    if expected_rows and row_match and not relevant_terms and not anchor_prefixes:
        return True

    return False


def load_build_provenance(db_path: Path) -> dict[str, Any]:
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return {
            "kb_metadata": {},
            "snapshot": {},
            "counts": {"chunks": 0, "statements": 0, "docs": 0},
        }

    try:
        connection.row_factory = sqlite3.Row
        kb_metadata = connection.execute(
            "SELECT kb_id, source_name, source_revision, extractor_version, "
            "built_at, notes FROM kb_metadata LIMIT 1"
        ).fetchone()
        snapshot = connection.execute(
            "SELECT snapshot_id, commit_sha, source_url, fetched_at, sha256 FROM snapshots LIMIT 1"
        ).fetchone()
        counts = connection.execute(
            "SELECT (SELECT COUNT(*) FROM chunks) AS chunk_count, "
            "(SELECT COUNT(*) FROM statements) AS statement_count, "
            "(SELECT COUNT(*) FROM docs) AS doc_count"
        ).fetchone()
    except sqlite3.Error:
        return {
            "kb_metadata": {},
            "snapshot": {},
            "counts": {"chunks": 0, "statements": 0, "docs": 0},
        }
    finally:
        connection.close()

    return {
        "kb_metadata": dict(kb_metadata) if kb_metadata is not None else {},
        "snapshot": dict(snapshot) if snapshot is not None else {},
        "counts": {
            "chunks": int(counts["chunk_count"]) if counts is not None else 0,
            "statements": int(counts["statement_count"]) if counts is not None else 0,
            "docs": int(counts["doc_count"]) if counts is not None else 0,
        },
    }


def load_trace_ids_by_context(path: Path | None) -> dict[str, list[str]]:
    if path is None or not path.exists():
        return {}

    by_context: dict[str, list[str]] = {}
    seen_by_context: dict[str, set[str]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        context = str(payload.get("context", "")).strip()
        trace_id = str(payload.get("trace_id", "")).strip()
        if not context or not trace_id:
            continue

        seen = seen_by_context.setdefault(context, set())
        if trace_id in seen:
            continue
        seen.add(trace_id)
        by_context.setdefault(context, []).append(trace_id)

    return by_context
