from __future__ import annotations

from typing import Any


def _clean(value: Any) -> str:
    return str(value or "").strip().lower()


def assess_decomposition(
    *,
    target_id: str,
    synth: dict[str, Any],
    amplification: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    constructs = synth.get("construct_scope") if isinstance(synth, dict) else []
    construct_list = (
        [str(value).strip() for value in constructs] if isinstance(constructs, list) else []
    )
    amplification_text = _clean(
        amplification.get("guideline_amplification_text") if isinstance(amplification, dict) else ""
    )
    tags = [str(value).strip() for value in list(metadata.get("tags") or []) if str(value).strip()]
    issues: list[str] = []
    score = 0
    if len(construct_list) >= 3:
        issues.append("multiple_construct_families")
        score += 2
    if amplification_text.count(" shall ") >= 2 or amplification_text.count(" should ") >= 2:
        issues.append("multiple_normative_clauses")
        score += 2
    if any(
        token in amplification_text
        for token in (" rather than ", " in particular ", " together with ")
    ):
        issues.append("composite_rule_connectors")
        score += 1
    if len(tags) >= 5:
        issues.append("broad_metadata_surface")
        score += 1
    status = "split_candidate" if score >= 3 else ("review" if score else "pass")
    return {
        "target_id": target_id,
        "status": status,
        "score": score,
        "issues": issues,
        "construct_count": len(construct_list),
    }
