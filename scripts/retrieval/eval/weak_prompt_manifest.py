from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WeakPromptWeights:
    off_target_trend: float = 0.45
    citation_readiness_regression: float = 0.35
    mrr_regression: float = 0.20


def _group_cases_by_prompt(payload: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in list(payload.get("cases", [])):
        if not isinstance(row, dict):
            continue
        prompt_id = str(row.get("prompt_id", "")).strip()
        mode = str(row.get("mode", "")).strip().lower()
        if not prompt_id or not mode:
            continue
        grouped.setdefault(prompt_id, {})[mode] = row
    return grouped


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _normalize_positive(values: list[float]) -> list[float]:
    if not values:
        return []
    upper = max(values)
    if upper <= 1e-12:
        return [0.0 for _ in values]
    return [max(0.0, value) / upper for value in values]


def build_weak_prompt_manifest(
    *,
    eval_payload: dict[str, Any],
    comparator_payload: dict[str, Any] | None,
    top_n: int = 10,
    weights: WeakPromptWeights | None = None,
) -> dict[str, Any]:
    resolved_weights = weights or WeakPromptWeights()
    grouped = _group_cases_by_prompt(eval_payload)
    comparator_grouped = _group_cases_by_prompt(comparator_payload or {})

    rows: list[dict[str, Any]] = []
    for prompt_id, modes in sorted(grouped.items()):
        current_hybrid = modes.get("hybrid")
        if current_hybrid is None:
            continue

        prior_hybrid = comparator_grouped.get(prompt_id, {}).get("hybrid")
        current_mrr = _to_float(current_hybrid.get("mrr_at_k", 0.0))
        prior_mrr = _to_float(prior_hybrid.get("mrr_at_k", 0.0)) if prior_hybrid else current_mrr

        current_row_hit = _to_float(current_hybrid.get("row_hit_rate", 0.0))
        prior_row_hit = (
            _to_float(prior_hybrid.get("row_hit_rate", 0.0)) if prior_hybrid else current_row_hit
        )

        off_target_proxy = max(0.0, 1.0 - current_row_hit)
        prior_off_target_proxy = max(0.0, 1.0 - prior_row_hit)
        off_target_trend = max(0.0, off_target_proxy - prior_off_target_proxy)
        citation_readiness_regression = max(0.0, prior_row_hit - current_row_hit)
        mrr_regression = max(0.0, prior_mrr - current_mrr)

        rows.append(
            {
                "prompt_id": prompt_id,
                "slice": str(current_hybrid.get("slice", "")).strip(),
                "expect_abstain": bool(current_hybrid.get("expect_abstain", False)),
                "off_target_trend": off_target_trend,
                "citation_readiness_regression": citation_readiness_regression,
                "mrr_regression": mrr_regression,
                "current_mrr": current_mrr,
                "prior_mrr": prior_mrr,
                "current_row_hit": current_row_hit,
                "prior_row_hit": prior_row_hit,
            }
        )

    off_target_norm = _normalize_positive([row["off_target_trend"] for row in rows])
    citation_norm = _normalize_positive([row["citation_readiness_regression"] for row in rows])
    mrr_norm = _normalize_positive([row["mrr_regression"] for row in rows])

    for row, off_n, cite_n, mrr_n in zip(
        rows, off_target_norm, citation_norm, mrr_norm, strict=False
    ):
        composite = (
            (resolved_weights.off_target_trend * off_n)
            + (resolved_weights.citation_readiness_regression * cite_n)
            + (resolved_weights.mrr_regression * mrr_n)
        )
        row["off_target_trend_norm"] = round(off_n, 6)
        row["citation_readiness_regression_norm"] = round(cite_n, 6)
        row["mrr_regression_norm"] = round(mrr_n, 6)
        row["composite_risk_score"] = round(float(composite), 6)

    rows.sort(
        key=lambda row: (
            -float(row["composite_risk_score"]),
            -float(row["off_target_trend"]),
            -float(row["citation_readiness_regression"]),
            row["prompt_id"],
        )
    )
    weak_prompts = rows[: max(1, int(top_n))]

    return {
        "schema_version": 1,
        "scoring": {
            "off_target_trend_weight": resolved_weights.off_target_trend,
            "citation_readiness_regression_weight": resolved_weights.citation_readiness_regression,
            "mrr_regression_weight": resolved_weights.mrr_regression,
            "primary_mode": "hybrid",
        },
        "top_n": int(top_n),
        "weak_prompt_ids": [str(row["prompt_id"]) for row in weak_prompts],
        "weak_prompts": weak_prompts,
    }


def write_weak_prompt_manifest(*, path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
