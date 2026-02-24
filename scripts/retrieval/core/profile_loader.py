from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_retrieval_profile(profile_path: Path) -> dict[str, Any]:
    with profile_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"Retrieval profile must be a mapping: {profile_path}")
    return payload


def apply_profile_defaults(args: Any, profile: dict[str, Any]) -> None:
    hybrid = profile.get("hybrid") or {}
    semantic = profile.get("semantic") or {}
    models = profile.get("models") or {}
    if isinstance(hybrid, dict):
        args.hybrid_fusion_method = hybrid.get("fusion_method", args.hybrid_fusion_method)
        args.hybrid_rrf_k = int(hybrid.get("rrf_k", args.hybrid_rrf_k))
        args.hybrid_rrf_window = int(hybrid.get("rrf_window", args.hybrid_rrf_window))
        args.hybrid_candidate_policy = hybrid.get("candidate_policy", args.hybrid_candidate_policy)
        args.hybrid_rerank_pool_size = int(
            hybrid.get("rerank_pool_size", args.hybrid_rerank_pool_size)
        )
        args.hybrid_lexical_min = int(hybrid.get("lexical_min", args.hybrid_lexical_min))
        args.hybrid_semantic_min = int(hybrid.get("semantic_min", args.hybrid_semantic_min))
        args.hybrid_lexical_floor_count = int(
            hybrid.get("lexical_floor_count", args.hybrid_lexical_floor_count)
        )
        args.hybrid_lexical_floor_share = float(
            hybrid.get("lexical_floor_share", args.hybrid_lexical_floor_share)
        )
    if isinstance(semantic, dict):
        args.semantic_timeout_sec = float(semantic.get("timeout_sec", args.semantic_timeout_sec))
        args.semantic_retries = int(semantic.get("retries", args.semantic_retries))
    if isinstance(models, dict):
        args.embed_model_id = str(models.get("embed_model_id", args.embed_model_id))
        args.reranker_model_id = str(models.get("reranker_model_id", args.reranker_model_id))
