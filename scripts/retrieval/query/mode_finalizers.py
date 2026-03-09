from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from retrieval.core.fusion import apply_component_scores
from retrieval.query.result_payload import build_retrieval_result


def finalize_lexical_like_result(
    *,
    requested_mode: str,
    executed_mode: str,
    degraded: bool,
    degraded_reason: str | None,
    lexical_rows: list[dict[str, Any]],
    top_k: int,
    query_text: str,
    corpus: str,
    row_marker: str,
    effective_query_text: str,
    query_rewrite: dict[str, Any],
    semantic_retry_events: list[dict[str, Any]],
    score_definitions: dict[str, str],
    workload: dict[str, int],
    started: float,
    timing: dict[str, float],
    timing_payload: Callable[[float], dict[str, float]],
    row_projection_policy: Any,
    apply_corpus_row_policy: Callable[[list[dict[str, Any]], str, str], list[dict[str, Any]]],
    build_row_projection: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    apply_abstain_policy: Callable[
        [list[dict[str, Any]], Any], tuple[list[dict[str, Any]], dict[str, Any]]
    ],
    lexical_weight: float,
    semantic_weight: float,
    rerank_weight: float,
    scope: dict[str, Any],
    preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workload["lexical_pool_size"] = int(len(lexical_rows))
    workload["union_pool_size"] = int(len(lexical_rows))
    for row in lexical_rows:
        apply_component_scores(
            row,
            lexical_score=float(row.get("lexical_score", 0.0)),
            semantic_score=0.0,
            reranker_score=0.0,
            lexical_weight=lexical_weight,
            semantic_weight=semantic_weight,
            reranker_weight=rerank_weight,
        )
    rows = apply_corpus_row_policy(lexical_rows[:top_k], query_text=query_text, corpus=corpus)
    projection_started = time.perf_counter()
    row_projection_all = build_row_projection(rows)
    row_projection, abstain = apply_abstain_policy(row_projection_all, row_projection_policy)
    timing["projection_ms"] += (time.perf_counter() - projection_started) * 1000.0
    duration_ms = (time.perf_counter() - started) * 1000.0
    return build_retrieval_result(
        requested_mode=requested_mode,
        executed_mode=executed_mode,
        degraded=degraded,
        degraded_reason=degraded_reason,
        semantic_retry_events=semantic_retry_events,
        score_definitions=score_definitions,
        workload=workload,
        query_text=query_text,
        effective_query_text=effective_query_text,
        query_rewrite=query_rewrite,
        row_marker=row_marker,
        rows=rows,
        duration_ms=duration_ms,
        timing=timing_payload(duration_ms),
        row_projection=row_projection,
        row_projection_all=row_projection_all,
        abstain=abstain,
        scope=scope,
        preflight=preflight,
    )
