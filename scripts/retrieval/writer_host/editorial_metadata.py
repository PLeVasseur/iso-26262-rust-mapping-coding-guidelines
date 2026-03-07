from __future__ import annotations

from typing import Any

from retrieval.writer_host.chapter_routing import route_chapter
from retrieval.writer_host.evidence_quality_gate import evaluate_evidence_quality
from retrieval.writer_host.title_policy import build_review_question, derive_title


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _derive_family(tags: list[str], constructs: list[str], title: str) -> str:
    text = " ".join([title.lower()] + [value.lower() for value in tags + constructs])
    if any(
        token in text for token in ("atomic", "ordering", "fence", "thread", "sync", "concurrency")
    ):
        return "concurrency"
    if any(token in text for token in ("lint", "must_use", "attribute", "diagnostic")):
        return "attributes"
    if any(token in text for token in ("pattern", "binding", "match")):
        return "patterns"
    if any(token in text for token in ("pin", "trait", "type", "generic", "self", "interface")):
        return "types-and-traits"
    if any(token in text for token in ("lifetime", "borrow", "ownership", "drop", "alias")):
        return "ownership-and-destruction"
    if any(token in text for token in ("panic", "result", "error", "infallible", "catch_unwind")):
        return "exceptions-and-errors"
    if any(token in text for token in ("unsafe", "pointer", "raw", "provenance", "union", "ffi")):
        return "unsafety"
    return "expressions"


def build_editorial_metadata(
    *,
    target_id: str,
    query_text: str,
    synth: dict[str, Any],
    amplification: dict[str, Any],
    rationale: dict[str, Any],
    examples: dict[str, Any],
    metadata: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    existing = metadata.get("editorial_metadata") if isinstance(metadata, dict) else {}
    existing = existing if isinstance(existing, dict) else {}
    title = derive_title(
        target_id=target_id,
        synth=synth,
        amplification=amplification,
        metadata=metadata,
    )
    tags = [str(value).strip() for value in list(metadata.get("tags") or []) if str(value).strip()]
    constructs = [
        str(value).strip()
        for value in list(synth.get("construct_scope") or [])
        if str(value).strip()
    ]
    family = _derive_family(tags, constructs, title)
    routed = route_chapter(metadata=metadata, synth=synth, title=title, current_tags=tags)
    evidence_quality = evaluate_evidence_quality(
        target_id=target_id,
        query_text=query_text,
        synth=synth,
        metadata=metadata,
        evidence_rows=evidence_rows,
    )
    topic_keywords = sorted(
        {value.lower() for value in tags + constructs if len(value.strip()) >= 4}
    )[:8]
    review_question = build_review_question(
        title=title, chapter=str(routed.get("chapter", "expressions"))
    )
    return {
        "proposed_title": title,
        "review_question": review_question,
        "primary_construct_family": family,
        "secondary_construct_families": [],
        "candidate_chapter": str(routed.get("chapter", "expressions")),
        "chapter_reason": str(routed.get("reason", "")),
        "topic_keywords": topic_keywords,
        "published_overlap_hints": list(existing.get("published_overlap_hints") or []),
        "sibling_overlap_hints": list(existing.get("sibling_overlap_hints") or []),
        "evidence_quality_status": str(evidence_quality.get("status", "pass")),
        "evidence_quality_issues": list(evidence_quality.get("issues") or []),
        "query_text": _clean(query_text),
        "rationale_summary": _clean(rationale.get("rationale_text", ""))[:200],
        "example_focus": _clean(examples.get("non_compliant_narrative", ""))[:200],
    }
