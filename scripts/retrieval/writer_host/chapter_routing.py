from __future__ import annotations

import re
from typing import Any

from retrieval.writer_host.reviewer_taxonomy import (
    expected_reviewer_chapter,
    normalize_reviewer_chapter,
)


def _clean(value: Any) -> str:
    return str(value or "").strip().lower()


def _flatten(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_clean(value) for value in values if _clean(value)]


def route_chapter(
    *,
    metadata: dict[str, Any],
    synth: dict[str, Any],
    title: str,
    current_tags: list[str] | None = None,
) -> dict[str, Any]:
    editorial = metadata.get("editorial_metadata") if isinstance(metadata, dict) else {}
    if not isinstance(editorial, dict):
        editorial = {}
    tags = _flatten(current_tags if current_tags is not None else metadata.get("tags"))
    constructs = _flatten(synth.get("construct_scope") if isinstance(synth, dict) else [])
    chapter_hint = _clean(editorial.get("candidate_chapter"))
    primary_family = _clean(editorial.get("primary_construct_family"))
    text = " ".join([_clean(title), " ".join(tags), " ".join(constructs), primary_family])

    decision = expected_reviewer_chapter(
        title=text,
        tags=tags,
        constructs=constructs,
        primary_family=primary_family,
        chapter_hint=chapter_hint,
    )
    return {
        "chapter": normalize_reviewer_chapter(decision.get("chapter")),
        "reason": str(decision.get("reason", "fallback")),
        "family": primary_family,
    }


def normalized_tags_for_domains(
    *, metadata: dict[str, Any], synth: dict[str, Any], chapter: str
) -> list[str]:
    editorial = metadata.get("editorial_metadata") if isinstance(metadata, dict) else {}
    families = []
    if isinstance(editorial, dict):
        families.extend(_flatten([editorial.get("primary_construct_family")]))
        families.extend(_flatten(editorial.get("secondary_construct_families")))
    tags = _flatten(metadata.get("tags") if isinstance(metadata, dict) else [])
    constructs = _flatten(synth.get("construct_scope") if isinstance(synth, dict) else [])
    text = " ".join(tags + constructs + families + [_clean(chapter)])

    out: list[str] = []
    if any(token in text for token in ("unsafe", "pointer", "provenance", "ffi", "union")):
        out.append("unsafe")
    if any(token in text for token in ("error", "panic", "result", "infallible")):
        out.append("defect")
    if any(token in text for token in ("atomic", "ordering", "thread", "sync", "concurrency")):
        out.append("concurrency")
    if any(token in text for token in ("lint", "must_use", "attribute", "diagnostic")):
        out.append("diagnostics")
    if any(token in text for token in ("pattern", "match", "binding")):
        out.append("patterns")
    if any(token in text for token in ("type", "trait", "pin", "nominal", "interface", "self")):
        out.append("types")
    if any(token in text for token in ("lifetime", "borrow", "ownership", "drop", "alias")):
        out.append("ownership")
    if not out:
        out.append("subset")
    return out


def chapter_quality_flags(
    *, chapter: str, metadata: dict[str, Any], synth: dict[str, Any]
) -> list[str]:
    flags: list[str] = []
    routing = route_chapter(
        metadata=metadata, synth=synth, title="", current_tags=list(metadata.get("tags") or [])
    )
    expected = str(routing.get("chapter", "")).strip()
    if expected and expected != chapter:
        flags.append(f"chapter_mismatch:{expected}")
    if chapter == "expressions":
        constructs = " ".join(
            _flatten(synth.get("construct_scope") if isinstance(synth, dict) else [])
        )
        if re.search(r"atomic|trait|pin|lint|pattern|lifetime|borrow", constructs):
            flags.append("chapter_too_generic_expressions")
    return flags
