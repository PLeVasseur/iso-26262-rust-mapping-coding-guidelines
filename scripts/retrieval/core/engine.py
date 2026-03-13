from __future__ import annotations

from dataclasses import dataclass

from retrieval.core.profile import (
    DEFAULT_HYBRID_RRF_K,
    HYBRID_CANDIDATE_POLICIES,
    HYBRID_CANDIDATE_POLICY_LEGACY,
    HYBRID_FUSION_METHODS,
    HYBRID_FUSION_WEIGHTED_V1,
)


@dataclass(frozen=True)
class RetrievalRuntimeConfig:
    top_k: int
    candidate_limit: int
    hybrid_fusion_method: str
    hybrid_rrf_k: int
    hybrid_rrf_window: int
    hybrid_lexical_floor_count: int
    hybrid_lexical_floor_share: float
    hybrid_candidate_policy: str
    hybrid_rerank_pool_size: int
    hybrid_lexical_min: int
    hybrid_semantic_min: int


def build_runtime_config(
    *,
    top_k: int,
    candidate_limit: int,
    hybrid_fusion_method: str = HYBRID_FUSION_WEIGHTED_V1,
    hybrid_rrf_k: int = DEFAULT_HYBRID_RRF_K,
    hybrid_rrf_window: int = 0,
    hybrid_lexical_floor_count: int = 0,
    hybrid_lexical_floor_share: float = 0.0,
    hybrid_candidate_policy: str = HYBRID_CANDIDATE_POLICY_LEGACY,
    hybrid_rerank_pool_size: int = 0,
    hybrid_lexical_min: int = 0,
    hybrid_semantic_min: int = 0,
) -> RetrievalRuntimeConfig:
    resolved_top_k = max(1, int(top_k))
    resolved_candidate_limit = max(resolved_top_k, int(candidate_limit))

    normalized_fusion_method = (
        str(hybrid_fusion_method).strip().lower() or HYBRID_FUSION_WEIGHTED_V1
    )
    if normalized_fusion_method not in HYBRID_FUSION_METHODS:
        raise ValueError(
            f"Unsupported hybrid fusion method: {hybrid_fusion_method}. "
            f"Expected one of {', '.join(HYBRID_FUSION_METHODS)}"
        )

    resolved_rrf_k = max(1, int(hybrid_rrf_k))
    resolved_rrf_window = int(hybrid_rrf_window)
    if resolved_rrf_window <= 0:
        resolved_rrf_window = max(resolved_top_k * 8, 64)

    normalized_candidate_policy = (
        str(hybrid_candidate_policy).strip().lower() or HYBRID_CANDIDATE_POLICY_LEGACY
    )
    if normalized_candidate_policy not in HYBRID_CANDIDATE_POLICIES:
        raise ValueError(
            f"Unsupported hybrid candidate policy: {hybrid_candidate_policy}. "
            f"Expected one of {', '.join(HYBRID_CANDIDATE_POLICIES)}"
        )

    return RetrievalRuntimeConfig(
        top_k=resolved_top_k,
        candidate_limit=resolved_candidate_limit,
        hybrid_fusion_method=normalized_fusion_method,
        hybrid_rrf_k=resolved_rrf_k,
        hybrid_rrf_window=resolved_rrf_window,
        hybrid_lexical_floor_count=max(0, int(hybrid_lexical_floor_count)),
        hybrid_lexical_floor_share=max(0.0, min(1.0, float(hybrid_lexical_floor_share))),
        hybrid_candidate_policy=normalized_candidate_policy,
        hybrid_rerank_pool_size=max(0, int(hybrid_rerank_pool_size)),
        hybrid_lexical_min=max(0, int(hybrid_lexical_min)),
        hybrid_semantic_min=max(0, int(hybrid_semantic_min)),
    )
