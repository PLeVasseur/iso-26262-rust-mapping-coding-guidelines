from __future__ import annotations

from typing import Any


def candidate_generation_payload(workload: dict[str, int]) -> dict[str, int]:
    return {
        "lexical_pool_size": int(workload["lexical_pool_size"]),
        "semantic_pool_size": int(workload["semantic_pool_size"]),
        "union_pool_size": int(workload["union_pool_size"]),
        "rerank_pool_size": int(workload["rerank_pool_size"]),
        "rerank_doc_count": int(workload["rerank_doc_count"]),
    }


def build_retrieval_result(
    *,
    requested_mode: str,
    executed_mode: str,
    degraded: bool,
    semantic_retry_events: list[dict[str, Any]],
    score_definitions: dict[str, str],
    workload: dict[str, int],
    query_text: str,
    effective_query_text: str,
    query_rewrite: dict[str, Any],
    row_marker: str,
    rows: list[dict[str, Any]],
    duration_ms: float,
    timing: dict[str, float],
    row_projection: list[dict[str, Any]],
    row_projection_all: list[dict[str, Any]],
    abstain: dict[str, Any],
    scope: dict[str, Any],
    degraded_reason: str | None = None,
    preflight: dict[str, Any] | None = None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "requested_mode": requested_mode,
        "executed_mode": executed_mode,
        "degraded": bool(degraded),
        "semantic_retry_events": semantic_retry_events,
        "score_definitions": score_definitions,
        "candidate_generation": candidate_generation_payload(workload),
        "query_text": query_text,
        "effective_query_text": effective_query_text,
        "query_rewrite": query_rewrite,
        "row_marker": row_marker,
        "row_count": len(rows),
        "duration_ms": round(duration_ms, 3),
        "timing": timing,
        "row_projection": row_projection,
        "row_projection_all": row_projection_all,
        "abstain": abstain,
        "scope": scope,
        "rows": rows,
    }
    if degraded_reason is not None:
        payload["degraded_reason"] = degraded_reason
    if preflight is not None:
        payload["preflight"] = preflight
    if extras:
        payload.update(extras)
    return payload
