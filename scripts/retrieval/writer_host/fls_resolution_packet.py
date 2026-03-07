from __future__ import annotations

import re
from typing import Any

TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "when",
    "into",
    "shall",
    "must",
    "code",
    "guideline",
    "rust",
    "paths",
    "path",
    "using",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list_text(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        text = _text(value)
        if text:
            out.append(text)
    return out


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_claim_phrases(draft: dict[str, Any]) -> list[str]:
    claim_map = draft.get("claim_to_evidence_map")
    if not isinstance(claim_map, list):
        return []
    out: list[str] = []
    for row in claim_map:
        if not isinstance(row, dict):
            continue
        claim_text = _text(row.get("claim_text"))
        if claim_text:
            out.append(claim_text)
            continue
        claim_id = _text(row.get("claim_id"))
        if claim_id:
            out.append(claim_id)
    return out[:8]


def _normalize_expected_domains(tags: list[str], title: str) -> list[str]:
    lowered = [value.lower() for value in tags]
    title_tokens = {token.lower() for token in re.findall(r"[A-Za-z0-9_]+", title)}
    domains: list[str] = []
    if any("unsafe" in value for value in lowered) or {"unsafe", "undefined", "ub"} & title_tokens:
        domains.append("unsafe")
    if any("error" in value for value in lowered) or "defect" in lowered:
        domains.append("defect")
    if "concurrency" in lowered or {"atomic", "thread", "send", "sync"} & title_tokens:
        domains.append("concurrency")
    if not domains:
        domains.append("expressions")
    return domains


def _tokenize(value: str, *, limit: int = 80) -> list[str]:
    out: list[str] = []
    for token in TOKEN_PATTERN.findall(value.lower()):
        if len(token) < 3 or token in STOPWORDS:
            continue
        if token in out:
            continue
        out.append(token)
        if len(out) >= limit:
            break
    return out


def build_resolution_packet(row: dict[str, Any]) -> dict[str, Any]:
    draft = _as_dict(row.get("draft"))
    amplification = _as_dict(row.get("amplification"))
    rationale = _as_dict(row.get("rationale"))
    examples = _as_dict(row.get("examples"))
    metadata = _as_dict(row.get("metadata"))

    fls_candidate = _as_dict(metadata.get("fls_candidate"))
    target_id = _text(draft.get("target_id"))
    claim_phrases = _extract_claim_phrases(draft)
    title = _text(fls_candidate.get("statement")) or (claim_phrases[0] if claim_phrases else "")
    title = title or f"Guideline {target_id}"
    tags = [value.lower() for value in _list_text(metadata.get("tags"))]
    amplification_text = _text(amplification.get("guideline_amplification_text"))
    rationale_text = _text(rationale.get("rationale_text"))
    non_compliant_narrative = _text(examples.get("non_compliant_narrative"))
    non_compliant_code = _text(examples.get("non_compliant_code"))
    compliant_narrative = _text(examples.get("compliant_narrative"))
    compliant_code = _text(examples.get("compliant_code"))

    field_terms = {
        "title": _tokenize(title, limit=20),
        "claim": _tokenize(" ".join(claim_phrases), limit=28),
        "rationale": _tokenize(rationale_text, limit=32),
        "amplification": _tokenize(amplification_text, limit=32),
        "non_compliant_narrative": _tokenize(non_compliant_narrative, limit=24),
        "compliant_narrative": _tokenize(compliant_narrative, limit=24),
        "construct_terms": _tokenize(title + " " + " ".join(tags), limit=24),
    }
    code_symbols = _tokenize(f"{non_compliant_code} {compliant_code}", limit=36)

    return {
        "target_id": target_id,
        "title": title,
        "tags": tags,
        "expected_domains": _normalize_expected_domains(tags, title),
        "amplification_text": amplification_text,
        "rationale_text": rationale_text,
        "non_compliant_narrative": non_compliant_narrative,
        "non_compliant_code": non_compliant_code,
        "compliant_narrative": compliant_narrative,
        "compliant_code": compliant_code,
        "claim_phrases": claim_phrases,
        "field_terms": field_terms,
        "code_symbols": code_symbols,
    }
