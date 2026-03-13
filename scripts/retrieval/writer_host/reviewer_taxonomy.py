from __future__ import annotations

import re
from typing import Any

CANONICAL_REVIEWER_CHAPTERS = (
    "attributes",
    "concurrency",
    "exceptions-and-errors",
    "expressions",
    "functions",
    "macros",
    "ownership-and-destruction",
    "patterns",
    "types-and-traits",
    "unsafety",
)

CHAPTER_ALIASES = {
    "attribute": "attributes",
    "diagnostics": "attributes",
    "attributes-and-diagnostics": "attributes",
    "error-handling": "exceptions-and-errors",
    "exceptions": "exceptions-and-errors",
    "errors": "exceptions-and-errors",
    "ownership": "ownership-and-destruction",
    "ownership-and-borrowing": "ownership-and-destruction",
    "lifetimes": "ownership-and-destruction",
    "types": "types-and-traits",
    "traits": "types-and-traits",
    "unsafe-code": "unsafety",
    "unsafe": "unsafety",
}

KEYWORD_PRIORITY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("concurrency", ("atomic", "ordering", "thread", "sync", "fence", "concurrency")),
    ("attributes", ("lint", "must_use", "diagnostic", "attribute", "forbid", "deny")),
    ("exceptions-and-errors", ("panic", "result", "error", "expect", "defensive failure")),
    ("patterns", ("pattern", "or-pattern", "match", "binding", "precedence")),
    (
        "ownership-and-destruction",
        ("lifetime", "borrow", "ownership", "alias", "drop", "destruction", "elision"),
    ),
    (
        "types-and-traits",
        ("trait", "pin", "type", "generic", "nominal", "interface", "strong typing"),
    ),
    ("macros", ("macro", "macro_rules", "proc_macro")),
    ("unsafety", ("unsafe", "pointer", "provenance", "raw", "dereference", "union", "ffi")),
)


def _clean(value: Any) -> str:
    return str(value or "").strip().lower()


def _has_keyword(text: str, *keywords: str) -> bool:
    for keyword in keywords:
        normalized = _clean(keyword)
        if not normalized:
            continue
        pattern = r"(^|[^a-z0-9])" + re.escape(normalized) + r"([^a-z0-9]|$)"
        if re.search(pattern, text):
            return True
    return False


def canonical_reviewer_chapters() -> tuple[str, ...]:
    return CANONICAL_REVIEWER_CHAPTERS


def normalize_reviewer_chapter(value: Any) -> str:
    text = _clean(value)
    if not text:
        return "expressions"
    text = CHAPTER_ALIASES.get(text, text)
    return text if text in CANONICAL_REVIEWER_CHAPTERS else text


def is_canonical_reviewer_chapter(value: Any) -> bool:
    return normalize_reviewer_chapter(value) in CANONICAL_REVIEWER_CHAPTERS


def expected_reviewer_chapter(
    *,
    title: str,
    tags: list[str] | None = None,
    constructs: list[str] | None = None,
    primary_family: str = "",
    chapter_hint: str = "",
) -> dict[str, str]:
    cleaned_hint = _clean(chapter_hint)
    normalized_hint = normalize_reviewer_chapter(cleaned_hint) if cleaned_hint else ""
    if normalized_hint in CANONICAL_REVIEWER_CHAPTERS:
        return {"chapter": normalized_hint, "reason": "chapter_hint"}
    text = " ".join(
        part
        for part in [
            _clean(title),
            " ".join(_clean(value) for value in list(tags or [])),
            " ".join(_clean(value) for value in list(constructs or [])),
            _clean(primary_family),
        ]
        if part
    )
    for chapter, keywords in KEYWORD_PRIORITY:
        if any(keyword in text for keyword in keywords):
            return {"chapter": chapter, "reason": f"keyword:{keywords[0]}"}
    return {"chapter": "expressions", "reason": "fallback"}


def classify_reviewer_family(*, title: str, tags: list[str], constructs: list[str]) -> str:
    title_text = _clean(title)
    tag_text = " ".join(_clean(v) for v in tags)
    construct_text = " ".join(_clean(v) for v in constructs)
    scoped_text = " ".join(part for part in (title_text, tag_text) if part)
    text = " ".join(part for part in (scoped_text, construct_text) if part)
    if _has_keyword(scoped_text, "extern", "abi", "ffi", "extern-blocks"):
        return "extern_abi"
    if _has_keyword(
        scoped_text, "pin", "pinning", "address-sensitive", "stable address", "api-design"
    ):
        return "pinning_interfaces"
    if _has_keyword(scoped_text, "must_use", "lint", "forbid", "deny"):
        return "diagnostics_policy"
    if _has_keyword(scoped_text, "result", "expect", "panic", "error"):
        return "exceptions_errors"
    if _has_keyword(text, "unsafe trait", "raw pointer", "dereference", "union"):
        return "unsafety_boundary"
    if _has_keyword(text, "lifetime", "borrow", "ownership", "alias", "elision"):
        return "ownership_aliasing"
    if _has_keyword(text, "target_feature", "strong typing", "nominal", "interface"):
        return "architecture_types"
    if _has_keyword(
        text, "provenance", "with_addr", "addr", "integer-derived", "exposed provenance"
    ):
        return "strict_provenance"
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:48] or "general"


def root_index_contains_chapter(*, index_text: str, chapter: str) -> bool:
    normalized = normalize_reviewer_chapter(chapter)
    patterns = [f"{normalized}/index", f"coding-guidelines/{normalized}/index"]
    return any(pattern in index_text for pattern in patterns)
