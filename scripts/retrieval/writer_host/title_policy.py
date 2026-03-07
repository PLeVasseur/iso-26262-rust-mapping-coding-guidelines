from __future__ import annotations

import re
from typing import Any

_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_:#\-]*")
_LEAK_PATTERNS = (
    re.compile(r"\bfor this run\b", re.IGNORECASE),
    re.compile(r"\boff-target\b", re.IGNORECASE),
    re.compile(r"\bcited evidence\b", re.IGNORECASE),
    re.compile(r"\bdescribes?\b", re.IGNORECASE),
    re.compile(r"\bthe rust reference\b", re.IGNORECASE),
    re.compile(r"\brust core library documentation\b", re.IGNORECASE),
)
_LEADIN_PATTERNS = (
    re.compile(r"^the cited evidence .*?:", re.IGNORECASE),
    re.compile(r"^the provided evidence .*?:", re.IGNORECASE),
)
_TITLE_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "shall",
    "should",
    "must",
    "code",
    "rust",
    "using",
    "used",
    "where",
    "when",
    "into",
    "rather",
    "than",
}


def _clean(value: Any) -> str:
    return _WS_RE.sub(" ", str(value or "").replace("`", "").strip())


def title_leakage_codes(title: str) -> list[str]:
    text = _clean(title)
    if not text:
        return ["empty_title"]
    codes: list[str] = []
    for pattern in _LEAK_PATTERNS:
        if pattern.search(text):
            codes.append("title_editorial_leakage")
            break
    for pattern in _LEADIN_PATTERNS:
        if pattern.search(text):
            codes.append("title_process_note")
            break
    lowered = text.lower()
    if lowered.startswith(("the ", "every ", "a ")) and len(text.split()) > 12:
        codes.append("title_source_sentence_shape")
    if len(text) > 140:
        codes.append("title_too_long")
    return sorted(dict.fromkeys(codes))


def looks_like_rule_title(title: str) -> bool:
    return not title_leakage_codes(title)


def build_review_question(*, title: str, chapter: str) -> str:
    cleaned = _clean(title)
    if not cleaned:
        return f"Does this {chapter} guideline express one enforceable review question?"
    return f"Does the code satisfy this rule: {cleaned}?"


def _keywords_from_text(text: str, *, limit: int = 4) -> list[str]:
    out: list[str] = []
    for token in _TOKEN_RE.findall(text.lower()):
        if len(token) < 4 or token in _TITLE_STOPWORDS:
            continue
        if token in out:
            continue
        out.append(token)
        if len(out) >= limit:
            break
    return out


def _construct_phrase(construct_scope: list[str]) -> str:
    cleaned = [_clean(item) for item in construct_scope if _clean(item)]
    if not cleaned:
        return "the relevant construct"
    primary = cleaned[0]
    primary = re.sub(r"^[#`\[]+|[#`\]]+$", "", primary).strip()
    return primary or "the relevant construct"


def derive_title(
    *,
    target_id: str,
    synth: dict[str, Any],
    amplification: dict[str, Any],
    metadata: dict[str, Any],
) -> str:
    editorial = metadata.get("editorial_metadata") if isinstance(metadata, dict) else None
    if isinstance(editorial, dict):
        proposed = _clean(editorial.get("proposed_title"))
        if proposed and looks_like_rule_title(proposed):
            return proposed

    construct_scope = synth.get("construct_scope") if isinstance(synth, dict) else []
    construct_text = _construct_phrase(construct_scope if isinstance(construct_scope, list) else [])
    mitigation = _clean(synth.get("mitigation"))
    if mitigation:
        lowered = mitigation.lower()
        for prefix in (
            "prefer ",
            "require ",
            "use ",
            "mark ",
            "encode ",
            "expose ",
            "avoid ",
            "separate ",
        ):
            if lowered.startswith(prefix):
                return mitigation.rstrip(".")
        key_terms = _keywords_from_text(mitigation)
        if key_terms:
            return f"Constrain {construct_text} to {' '.join(key_terms[:2])}".strip()

    amplification_text = _clean(amplification.get("guideline_amplification_text"))
    if amplification_text:
        first_sentence = amplification_text.split(".", 1)[0].strip()
        if first_sentence and len(first_sentence) <= 120 and looks_like_rule_title(first_sentence):
            return first_sentence

    claim_map = synth.get("claim_to_evidence_map") if isinstance(synth, dict) else []
    if isinstance(claim_map, list):
        for row in claim_map:
            if not isinstance(row, dict):
                continue
            claim = _clean(row.get("claim_text"))
            if claim and looks_like_rule_title(claim):
                return claim[:120].rstrip(".")

    return f"Narrow {construct_text} to one safety-relevant rule for {target_id}"[:120]
