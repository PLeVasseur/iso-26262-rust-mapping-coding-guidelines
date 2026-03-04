from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from retrieval.core.fusion import apply_component_scores
from retrieval.core.profile import HYBRID_FUSION_WEIGHTED_V2
from retrieval.query.hybrid_pipeline import run_hybrid_pipeline
from retrieval.query.result_payload import build_retrieval_result


def finalize_semantic_mode(
    *,
    mode: str,
    semantic_rows: list[dict[str, Any]],
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
    preflight: dict[str, Any],
    apply_corpus_row_policy: Callable[[list[dict[str, Any]], str, str], list[dict[str, Any]]],
    build_row_projection: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    apply_abstain_policy: Callable[
        [list[dict[str, Any]], Any], tuple[list[dict[str, Any]], dict[str, Any]]
    ],
    row_projection_policy: Any,
    row_identity: Callable[[dict[str, Any]], str],
    lexical_weight: float,
    semantic_weight: float,
    rerank_weight: float,
) -> dict[str, Any]:
    for row in semantic_rows:
        apply_component_scores(
            row,
            lexical_score=0.0,
            semantic_score=float(row.get("semantic_score", 0.0)),
            reranker_score=float(row.get("reranker_score", 0.0)),
            lexical_weight=lexical_weight,
            semantic_weight=semantic_weight,
            reranker_weight=rerank_weight,
        )
    semantic_rows.sort(
        key=lambda row: (
            -float(row.get("final_score", 0.0)),
            -float(row.get("reranker_score", 0.0)),
            row_identity(row),
        )
    )
    rows = apply_corpus_row_policy(semantic_rows[:top_k], query_text, corpus)
    workload["union_pool_size"] = int(len(semantic_rows))
    projection_started = time.perf_counter()
    row_projection_all = build_row_projection(rows)
    row_projection, abstain = apply_abstain_policy(row_projection_all, row_projection_policy)
    timing["projection_ms"] += (time.perf_counter() - projection_started) * 1000.0
    duration_ms = (time.perf_counter() - started) * 1000.0
    return build_retrieval_result(
        requested_mode=mode,
        executed_mode=mode,
        degraded=False,
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
        preflight=preflight,
    )


def finalize_hybrid_mode(
    *,
    mode: str,
    lexical_rows: list[dict[str, Any]],
    semantic_rows: list[dict[str, Any]],
    candidate_limit: int,
    normalized_candidate_policy: str,
    normalized_fusion_method: str,
    effective_query_text: str,
    top_k: int,
    resolved_hybrid_rerank_pool_size: int,
    resolved_hybrid_lexical_min: int,
    resolved_hybrid_semantic_min: int,
    resolved_lexical_floor_count: int,
    resolved_lexical_floor_share: float,
    resolved_rrf_k: int,
    resolved_rrf_window: int,
    lexical_weight: float,
    semantic_weight: float,
    rerank_weight: float,
    weighted_v2_lexical_weight: float,
    weighted_v2_semantic_weight: float,
    weighted_v2_rerank_weight: float,
    row_identity: Callable[[dict[str, Any]], str],
    rerank_documents: Callable[[str, list[str]], list[float]],
    timing: dict[str, float],
    workload: dict[str, int],
    query_text: str,
    corpus: str,
    row_marker: str,
    query_rewrite: dict[str, Any],
    semantic_retry_events: list[dict[str, Any]],
    score_definitions: dict[str, str],
    fusion_params: dict[str, Any],
    started: float,
    timing_payload: Callable[[float], dict[str, float]],
    preflight: dict[str, Any],
    apply_corpus_row_policy: Callable[[list[dict[str, Any]], str, str], list[dict[str, Any]]],
    build_row_projection: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    apply_abstain_policy: Callable[
        [list[dict[str, Any]], Any], tuple[list[dict[str, Any]], dict[str, Any]]
    ],
    row_projection_policy: Any,
) -> dict[str, Any]:
    hybrid_rows, fusion_debug, union_pool_size = run_hybrid_pipeline(
        lexical_rows=lexical_rows,
        semantic_rows=semantic_rows,
        candidate_limit=candidate_limit,
        normalized_candidate_policy=normalized_candidate_policy,
        normalized_fusion_method=normalized_fusion_method,
        effective_query_text=effective_query_text,
        top_k=top_k,
        resolved_hybrid_rerank_pool_size=resolved_hybrid_rerank_pool_size,
        resolved_hybrid_lexical_min=resolved_hybrid_lexical_min,
        resolved_hybrid_semantic_min=resolved_hybrid_semantic_min,
        resolved_lexical_floor_count=resolved_lexical_floor_count,
        resolved_lexical_floor_share=resolved_lexical_floor_share,
        resolved_rrf_k=resolved_rrf_k,
        resolved_rrf_window=resolved_rrf_window,
        lexical_weight=lexical_weight,
        semantic_weight=semantic_weight,
        rerank_weight=rerank_weight,
        weighted_v2_lexical_weight=weighted_v2_lexical_weight,
        weighted_v2_semantic_weight=weighted_v2_semantic_weight,
        weighted_v2_rerank_weight=weighted_v2_rerank_weight,
        row_identity=row_identity,
        rerank_documents=rerank_documents,
        timing=timing,
        workload=workload,
    )
    rows = apply_corpus_row_policy(hybrid_rows[:top_k], query_text, corpus)
    workload["union_pool_size"] = int(union_pool_size)
    projection_started = time.perf_counter()
    row_projection_all = build_row_projection(rows)
    row_projection, abstain = apply_abstain_policy(row_projection_all, row_projection_policy)
    timing["projection_ms"] += (time.perf_counter() - projection_started) * 1000.0
    duration_ms = (time.perf_counter() - started) * 1000.0

    return build_retrieval_result(
        requested_mode=mode,
        executed_mode=mode,
        degraded=False,
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
        preflight=preflight,
        extras={
            "fusion_method": normalized_fusion_method,
            "fusion_params": fusion_params,
            "fusion_debug": fusion_debug,
            "fusion_weights": {
                "lexical": (
                    weighted_v2_lexical_weight
                    if normalized_fusion_method == HYBRID_FUSION_WEIGHTED_V2
                    else lexical_weight
                ),
                "semantic": (
                    weighted_v2_semantic_weight
                    if normalized_fusion_method == HYBRID_FUSION_WEIGHTED_V2
                    else semantic_weight
                ),
                "reranker": (
                    weighted_v2_rerank_weight
                    if normalized_fusion_method == HYBRID_FUSION_WEIGHTED_V2
                    else rerank_weight
                ),
            },
        },
    )
