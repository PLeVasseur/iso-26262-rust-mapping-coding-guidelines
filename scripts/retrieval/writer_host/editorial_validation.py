from __future__ import annotations

from typing import Any

from retrieval.writer_host.chapter_routing import chapter_quality_flags
from retrieval.writer_host.title_policy import title_leakage_codes


def validate_editorial_bundle(
    *,
    target_id: str,
    draft: dict[str, Any],
    metadata: dict[str, Any],
    synth: dict[str, Any],
    evidence_quality: dict[str, Any],
    decomposition: dict[str, Any],
) -> list[str]:
    violations: list[str] = []
    title = str(draft.get("title", "")).strip()
    chapter = str(draft.get("chapter", "")).strip()
    review_question = str(draft.get("review_question", "")).strip()
    for code in title_leakage_codes(title):
        violations.append(code)
    for code in chapter_quality_flags(chapter=chapter, metadata=metadata, synth=synth):
        violations.append(code)
    if not review_question:
        violations.append("review_question_missing")
    if evidence_quality.get("blocked"):
        violations.append("evidence_quality_blocked")
    if str(decomposition.get("status", "")) == "split_candidate":
        violations.append("composite_rule_split_candidate")
    if title and len(title.split()) > 18:
        violations.append("title_too_wordy")
    return sorted(dict.fromkeys(violations))
