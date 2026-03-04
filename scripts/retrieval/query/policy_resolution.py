from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from retrieval.core.policy_loader import load_eval_policy
from retrieval.corpora.registry import get_corpus_adapter

ROW_PROJECTION_THRESHOLDS = {
    "1a": 0.015,
    "1b": 0.015,
    "1c": 0.015,
    "1d": 0.015,
    "1e": 0.015,
    "1f": 0.015,
    "1g": 0.015,
    "1h": 0.015,
    "1i": 0.020,
}
ROW_PROJECTION_TOP_SCORE_FLOOR = 0.0
ROW_PROJECTION_MIN_EVIDENCE_HITS = 1
ROW_PROJECTION_MARGIN = 0.005


@dataclass(frozen=True)
class RowProjectionPolicy:
    thresholds: dict[str, float]
    top_score_floor: float
    min_evidence_hits: int
    margin: float


def row_projection_policy_from_globals() -> RowProjectionPolicy:
    return RowProjectionPolicy(
        thresholds={
            str(marker).strip().lower(): float(value)
            for marker, value in ROW_PROJECTION_THRESHOLDS.items()
            if str(marker).strip()
        },
        top_score_floor=float(ROW_PROJECTION_TOP_SCORE_FLOOR),
        min_evidence_hits=max(1, int(ROW_PROJECTION_MIN_EVIDENCE_HITS)),
        margin=max(0.0, float(ROW_PROJECTION_MARGIN)),
    )


def resolve_row_projection_policy(*, root: Path, corpus: str) -> RowProjectionPolicy:
    policy = row_projection_policy_from_globals()
    policy_path = (root / str(get_corpus_adapter(corpus).config.default_eval_policy_path)).resolve()
    if policy_path.exists():
        loaded = load_eval_policy(policy_path)
        projection = loaded.get("projection_thresholds") or {}
        thresholds = dict(policy.thresholds)
        if isinstance(projection, dict):
            resolved_thresholds = {
                str(marker).strip().lower(): float(value)
                for marker, value in projection.items()
                if str(marker).strip()
            }
            default_threshold = float(
                resolved_thresholds.get(
                    "default",
                    min(thresholds.values()) if thresholds else 0.015,
                )
            )
            row_markers = tuple(f"1{chr(ord('a') + idx)}" for idx in range(9))
            thresholds = {
                marker: float(resolved_thresholds.get(marker, default_threshold))
                for marker in row_markers
            }

        abstain_policy = loaded.get("abstain_policy") or {}
        top_floor = policy.top_score_floor
        min_hits = policy.min_evidence_hits
        margin = policy.margin
        if isinstance(abstain_policy, dict):
            configured_top_floor = abstain_policy.get("top_score_floor")
            configured_min_hits = abstain_policy.get("min_evidence_hits")
            configured_margin = abstain_policy.get("margin")
            if configured_top_floor is not None:
                top_floor = max(0.0, float(configured_top_floor))
            if configured_min_hits is not None:
                min_hits = max(1, int(configured_min_hits))
            if configured_margin is not None:
                margin = max(0.0, float(configured_margin))

        policy = RowProjectionPolicy(
            thresholds=thresholds,
            top_score_floor=float(top_floor),
            min_evidence_hits=max(1, int(min_hits)),
            margin=max(0.0, float(margin)),
        )

    env_top_floor = str(os.getenv("SQLKB_ROW_PROJECTION_TOP_SCORE_FLOOR", "")).strip()
    env_min_hits = str(os.getenv("SQLKB_ROW_PROJECTION_MIN_EVIDENCE_HITS", "")).strip()
    env_margin = str(os.getenv("SQLKB_ROW_PROJECTION_MARGIN", "")).strip()
    top_floor = policy.top_score_floor
    min_hits = policy.min_evidence_hits
    margin = policy.margin
    if env_top_floor:
        top_floor = max(0.0, float(env_top_floor))
    if env_min_hits:
        min_hits = max(1, int(env_min_hits))
    if env_margin:
        margin = max(0.0, float(env_margin))

    return RowProjectionPolicy(
        thresholds=dict(policy.thresholds),
        top_score_floor=float(top_floor),
        min_evidence_hits=max(1, int(min_hits)),
        margin=max(0.0, float(margin)),
    )
