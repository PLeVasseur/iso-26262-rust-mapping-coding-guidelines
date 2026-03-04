from __future__ import annotations

from typing import Any

from retrieval.core.profile import HYBRID_FUSION_RRF_V1, HYBRID_FUSION_WEIGHTED_V2


def build_fusion_metadata(
    *,
    normalized_fusion_method: str,
    resolved_rrf_k: int,
    resolved_rrf_window: int,
    resolved_lexical_floor_count: int,
    resolved_lexical_floor_share: float,
    normalized_candidate_policy: str,
    resolved_hybrid_rerank_pool_size: int,
    resolved_hybrid_lexical_min: int,
    resolved_hybrid_semantic_min: int,
    lexical_weight: float,
    semantic_weight: float,
    rerank_weight: float,
    weighted_v2_lexical_weight: float,
    weighted_v2_semantic_weight: float,
    weighted_v2_rerank_weight: float,
) -> tuple[dict[str, str], dict[str, Any]]:
    score_definitions = {
        "lexical_score": "Normalized lexical relevance from FTS and token overlap",
        "semantic_score": "Normalized embedding cosine similarity to query",
        "reranker_score": "Normalized cross-encoder reranker relevance",
        "final_score": "",
    }
    if normalized_fusion_method == HYBRID_FUSION_RRF_V1:
        score_definitions["final_score"] = (
            "Reciprocal rank fusion score across lexical/semantic/reranker lists"
        )
    elif normalized_fusion_method == HYBRID_FUSION_WEIGHTED_V2:
        score_definitions["final_score"] = (
            "Weighted-v2 score "
            f"({weighted_v2_lexical_weight:.2f}*lexical + "
            f"{weighted_v2_semantic_weight:.2f}*semantic + "
            f"{weighted_v2_rerank_weight:.2f}*reranker)"
        )
    else:
        score_definitions["final_score"] = (
            f"Weighted score ({lexical_weight:.2f}*lexical + "
            f"{semantic_weight:.2f}*semantic + {rerank_weight:.2f}*reranker)"
        )

    fusion_params = {
        "method": normalized_fusion_method,
        "rrf_k": int(resolved_rrf_k),
        "rrf_window": int(resolved_rrf_window),
        "lexical_floor_count": int(resolved_lexical_floor_count),
        "lexical_floor_share": float(resolved_lexical_floor_share),
        "candidate_policy": str(normalized_candidate_policy),
        "rerank_pool_size": int(resolved_hybrid_rerank_pool_size),
        "lexical_min": int(resolved_hybrid_lexical_min),
        "semantic_min": int(resolved_hybrid_semantic_min),
    }
    return score_definitions, fusion_params
