from __future__ import annotations

from typing import Any

TRAIT_QUERY_TERMS: tuple[str, ...] = (
    "trait",
    "abstraction",
    "architecture",
    "interface",
)

STYLE_QUERY_TERMS: tuple[str, ...] = (
    "style",
    "lint",
    "analyzability",
    "audit",
    "convention",
)

CONTROL_FLOW_QUERY_TERMS: tuple[str, ...] = (
    "match",
    "non-exhaustive",
    "non_exhaustive",
    "exhaustive",
    "branch",
    "control-flow",
    "control flow",
)

TRAIT_POSITIVE_MATCHERS: tuple[str, ...] = (
    "items/traits",
    "types/trait-object",
    "glossary",
)

TRAIT_NEGATIVE_MATCHERS: tuple[str, ...] = (
    "inline-assembly",
    "items/external-blocks",
)

STYLE_POSITIVE_MATCHERS: tuple[str, ...] = ("attributes/diagnostics",)

CONTROL_FLOW_POSITIVE_MATCHERS: tuple[str, ...] = (
    "expressions/match-expr",
    "attributes/type_system",
    "patterns",
)

STYLE_NEGATIVE_MATCHERS: tuple[str, ...] = (
    "items/external-blocks",
    "inline-assembly",
    "expressions/block-expr",
)

CONTROL_FLOW_NEGATIVE_MATCHERS: tuple[str, ...] = (
    "expressions/block-expr",
    "macro-ambiguity",
    "macros-by-example",
)

STYLE_PREFERRED_HEADINGS: tuple[str, ...] = (
    "lint check attributes",
    "lint groups",
    "lint reasons",
)

STYLE_PENALIZED_HEADINGS: tuple[str, ...] = (
    "must_use",
    "on_unimplemented",
)

CONTROL_FLOW_PREFERRED_HEADINGS: tuple[str, ...] = (
    "`match` expressions",
    "the `non_exhaustive` attribute",
)


def _blob(row: dict[str, Any]) -> str:
    return " ".join(
        (
            str(row.get("doc_path", "")),
            str(row.get("section_heading", "")),
            str(row.get("source_anchor", "")),
        )
    ).lower()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _score_row(
    row: dict[str, Any], *, positives: tuple[str, ...], negatives: tuple[str, ...]
) -> int:
    blob = _blob(row)
    positive = any(token in blob for token in positives)
    negative = any(token in blob for token in negatives)
    if positive and not negative:
        return 2
    if positive:
        return 1
    if negative:
        return -1
    return 0


def apply_intent_path_preference(
    rows: list[dict[str, Any]], *, query_text: str
) -> list[dict[str, Any]]:
    lowered = str(query_text).strip().lower()
    if not lowered:
        return rows

    positives: tuple[str, ...] = ()
    negatives: tuple[str, ...] = ()
    preferred_headings: tuple[str, ...] = ()
    penalized_headings: tuple[str, ...] = ()
    if _contains_any(lowered, STYLE_QUERY_TERMS):
        positives = STYLE_POSITIVE_MATCHERS
        negatives = STYLE_NEGATIVE_MATCHERS
        preferred_headings = STYLE_PREFERRED_HEADINGS
        penalized_headings = STYLE_PENALIZED_HEADINGS
    elif _contains_any(lowered, CONTROL_FLOW_QUERY_TERMS):
        positives = CONTROL_FLOW_POSITIVE_MATCHERS
        negatives = CONTROL_FLOW_NEGATIVE_MATCHERS
        preferred_headings = CONTROL_FLOW_PREFERRED_HEADINGS
    elif _contains_any(lowered, TRAIT_QUERY_TERMS):
        positives = TRAIT_POSITIVE_MATCHERS
        negatives = TRAIT_NEGATIVE_MATCHERS
    else:
        return rows

    decorated: list[tuple[int, int, dict[str, Any]]] = []
    for idx, row in enumerate(rows):
        score = _score_row(row, positives=positives, negatives=negatives)
        heading = str(row.get("section_heading", "")).strip().lower()
        if heading and any(token in heading for token in preferred_headings):
            score += 1
        if heading and any(token in heading for token in penalized_headings):
            score -= 1
        decorated.append((score, idx, row))
    decorated.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in decorated]
