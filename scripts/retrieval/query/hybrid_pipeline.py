from __future__ import annotations

from collections.abc import Callable
from typing import Any

from retrieval.core.candidate_policy import (
    apply_hybrid_candidate_policy_v2_rerank,
    apply_hybrid_lexical_floor_rerank,
)
from retrieval.core.fusion import apply_component_scores, apply_rrf_hybrid_scores
from retrieval.core.profile import (
    HYBRID_CANDIDATE_POLICY_V2,
    HYBRID_FUSION_RRF_V1,
    HYBRID_FUSION_WEIGHTED_V2,
)
from retrieval.query.semantic_math import min_max_normalize


def run_hybrid_pipeline(
    *,
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
) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    lexical_rows = lexical_rows[:candidate_limit]
    semantic_rows = semantic_rows[:candidate_limit]
    workload["lexical_pool_size"] = int(len(lexical_rows))
    workload["semantic_pool_size"] = int(len(semantic_rows))

    lexical_ids = [row_identity(row) for row in lexical_rows]
    lexical_values = [float(row.get("lexical_score", 0.0)) for row in lexical_rows]
    lexical_norm = min_max_normalize(lexical_values)
    lexical_score_by_id = dict(zip(lexical_ids, lexical_norm, strict=False))

    merged: dict[str, dict[str, Any]] = {}
    for row in semantic_rows:
        row_id = row_identity(row)
        merged[row_id] = dict(row)
        merged[row_id]["lexical_score"] = lexical_score_by_id.get(row_id, 0.0)

    for row in lexical_rows:
        row_id = row_identity(row)
        if row_id not in merged:
            merged[row_id] = dict(row)
            merged[row_id]["semantic_score"] = 0.0
            merged[row_id]["reranker_score"] = 0.0
        merged[row_id]["lexical_score"] = lexical_score_by_id.get(row_id, 0.0)

    candidate_policy_debug: dict[str, Any] = {
        "policy": normalized_candidate_policy,
        "enabled": False,
    }

    if normalized_candidate_policy == HYBRID_CANDIDATE_POLICY_V2:
        candidate_policy_debug = apply_hybrid_candidate_policy_v2_rerank(
            merged_rows=merged,
            lexical_rows=lexical_rows,
            semantic_rows=semantic_rows,
            query_text=effective_query_text,
            top_k=top_k,
            rerank_pool_size=resolved_hybrid_rerank_pool_size,
            lexical_min=resolved_hybrid_lexical_min,
            semantic_min=resolved_hybrid_semantic_min,
            row_identity=row_identity,
            rerank_documents=rerank_documents,
            normalize_scores=min_max_normalize,
            timing=timing,
            workload=workload,
        )
    else:
        candidate_policy_debug = apply_hybrid_lexical_floor_rerank(
            merged_rows=merged,
            lexical_rows=lexical_rows,
            semantic_rows=semantic_rows,
            query_text=effective_query_text,
            top_k=top_k,
            floor_count=resolved_lexical_floor_count,
            floor_share=resolved_lexical_floor_share,
            row_identity=row_identity,
            rerank_documents=rerank_documents,
            normalize_scores=min_max_normalize,
            timing=timing,
            workload=workload,
        )

    fusion_debug: dict[str, Any] = {}
    hybrid_rows: list[dict[str, Any]]
    if normalized_fusion_method == HYBRID_FUSION_RRF_V1:
        hybrid_rows, fusion_debug = apply_rrf_hybrid_scores(
            merged_rows=merged,
            lexical_rows=lexical_rows,
            semantic_rows=semantic_rows,
            rrf_k=resolved_rrf_k,
            rrf_window=resolved_rrf_window,
            row_identity=row_identity,
        )
        fusion_debug["candidate_policy"] = candidate_policy_debug
    else:
        use_weighted_v2 = normalized_fusion_method == HYBRID_FUSION_WEIGHTED_V2
        hybrid_rows = []
        for row in merged.values():
            if use_weighted_v2:
                apply_component_scores(
                    row,
                    lexical_score=float(row.get("lexical_score", 0.0)),
                    semantic_score=float(row.get("semantic_score", 0.0)),
                    reranker_score=float(row.get("reranker_score", 0.0)),
                    lexical_weight=weighted_v2_lexical_weight,
                    semantic_weight=weighted_v2_semantic_weight,
                    reranker_weight=weighted_v2_rerank_weight,
                )
            else:
                apply_component_scores(
                    row,
                    lexical_score=float(row.get("lexical_score", 0.0)),
                    semantic_score=float(row.get("semantic_score", 0.0)),
                    reranker_score=float(row.get("reranker_score", 0.0)),
                    lexical_weight=lexical_weight,
                    semantic_weight=semantic_weight,
                    reranker_weight=rerank_weight,
                )
            hybrid_rows.append(row)

        hybrid_rows.sort(
            key=lambda row: (
                -float(row.get("final_score", 0.0)),
                -float(row.get("reranker_score", 0.0)),
                row_identity(row),
            )
        )
        if candidate_policy_debug:
            fusion_debug["candidate_policy"] = candidate_policy_debug

    return hybrid_rows, fusion_debug, int(len(merged))
