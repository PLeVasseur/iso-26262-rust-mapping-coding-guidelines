from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from semantic_backend_client import SemanticBackendConfig
from sqlite_query_guardrails import GuardrailError


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _safe_slug(value: str, fallback: str) -> str:
    tokens = [token for token in re.findall(r"[a-z0-9]+", str(value).lower()) if token]
    if not tokens:
        return fallback
    slug = "-".join(tokens)
    clipped = slug[:80].strip("-")
    return clipped if clipped else fallback


def _resolve_root_relative_path(root: Path, raw: str) -> Path:
    path = Path(str(raw).strip())
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def resolve_review_artifact_path(
    *,
    root: Path,
    mode: str,
    query_text: str,
    prompt_id: str,
    save_response_path: str,
    save_response_dir: str,
    default_query_review_dir: str = ".cache/sqlite_kb/reports/rust_reference/query_reviews",
) -> Path | None:
    explicit_path = str(save_response_path).strip()
    explicit_dir = str(save_response_dir).strip()
    if explicit_path and explicit_dir:
        raise GuardrailError("Use either --save-response-path or --save-response-dir, not both")

    if not explicit_path and not explicit_dir:
        return None

    if explicit_path:
        return _resolve_root_relative_path(root, explicit_path)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    prompt_token = _safe_slug(prompt_id, "")
    if not prompt_token:
        prompt_token = _safe_slug(query_text, "adhoc")
    filename = f"{stamp}__{prompt_token}__{str(mode).strip().lower()}.json"
    directory = _resolve_root_relative_path(root, explicit_dir or default_query_review_dir)
    return directory / filename


def build_review_artifact_payload(
    *,
    mode: str,
    query_text: str,
    row_marker: str,
    prompt_id: str,
    top_k: int,
    candidate_limit: int,
    include_score_breakdown: bool,
    allow_degraded: bool,
    db_path: Path,
    contract_path: Path,
    query_log_root: Path,
    semantic_config: SemanticBackendConfig | None,
    semantic_retries: int,
    persist_semantic_cache: bool,
    allow_online_corpus_embedding: bool,
    response: dict[str, Any],
    review_artifact_schema_version: int = 1,
) -> dict[str, Any]:
    semantic_runtime: dict[str, Any] = {}
    if semantic_config is not None:
        semantic_runtime = {
            "base_url": str(semantic_config.base_url),
            "embed_base_url": str(semantic_config.embed_base_url or semantic_config.base_url),
            "rerank_base_url": str(semantic_config.rerank_base_url or semantic_config.base_url),
            "embed_model_id": str(semantic_config.embed_model_id),
            "reranker_model_id": str(semantic_config.reranker_model_id),
            "timeout_sec": float(semantic_config.timeout_sec),
            "semantic_retries": int(semantic_retries),
            "persist_semantic_cache": bool(persist_semantic_cache),
            "allow_online_corpus_embedding": bool(allow_online_corpus_embedding),
        }

    return {
        "schema_version": int(review_artifact_schema_version),
        "generated_at": _utc_now(),
        "prompt_id": str(prompt_id).strip(),
        "query": {
            "mode": str(mode).strip().lower(),
            "query_text": str(query_text),
            "row_marker": str(row_marker).strip().lower(),
            "top_k": int(top_k),
            "candidate_limit": int(candidate_limit),
            "include_score_breakdown": bool(include_score_breakdown),
            "allow_degraded": bool(allow_degraded),
        },
        "runtime": {
            "db_path": str(db_path),
            "contract_path": str(contract_path),
            "query_log_root": str(query_log_root),
            "backend_profile": "configured",
            "semantic": semantic_runtime,
        },
        "response": response,
    }


def persist_review_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
