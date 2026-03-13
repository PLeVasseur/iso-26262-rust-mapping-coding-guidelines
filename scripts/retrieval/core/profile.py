from __future__ import annotations

from dataclasses import dataclass

HYBRID_FUSION_WEIGHTED_V1 = "weighted-v1"
HYBRID_FUSION_WEIGHTED_V2 = "weighted-v2"
HYBRID_FUSION_RRF_V1 = "rrf-v1"
HYBRID_FUSION_METHODS = (
    HYBRID_FUSION_WEIGHTED_V1,
    HYBRID_FUSION_WEIGHTED_V2,
    HYBRID_FUSION_RRF_V1,
)

HYBRID_CANDIDATE_POLICY_LEGACY = "legacy"
HYBRID_CANDIDATE_POLICY_V2 = "v2"
HYBRID_CANDIDATE_POLICIES = (
    HYBRID_CANDIDATE_POLICY_LEGACY,
    HYBRID_CANDIDATE_POLICY_V2,
)

DEFAULT_HYBRID_RRF_K = 60


@dataclass(frozen=True)
class HybridRuntimeProfile:
    fusion_method: str
    rrf_k: int
    rrf_window: int
    candidate_policy: str
    rerank_pool_size: int
    lexical_min: int
    semantic_min: int
    lexical_floor_count: int
    lexical_floor_share: float
