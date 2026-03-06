from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from context.fls_lookup import search_fls_paragraphs

_STOPWORDS = {
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
}


def _tokens(text: str, *, limit: int) -> list[str]:
    values: list[str] = []
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text.lower()):
        if len(token) < 3 or token in _STOPWORDS:
            continue
        if token not in values:
            values.append(token)
        if len(values) >= limit:
            break
    return values


def build_query_variants(packet: dict[str, Any]) -> list[dict[str, str]]:
    field_terms = packet.get("field_terms") if isinstance(packet.get("field_terms"), dict) else {}
    title_tokens = list(field_terms.get("title") or []) or _tokens(
        str(packet.get("title", "")), limit=10
    )
    rationale_tokens = list(field_terms.get("rationale") or []) or _tokens(
        f"{packet.get('amplification_text', '')} {packet.get('rationale_text', '')}",
        limit=14,
    )
    narrative_tokens = list(field_terms.get("non_compliant_narrative") or []) + list(
        field_terms.get("compliant_narrative") or []
    )
    if not narrative_tokens:
        narrative_tokens = _tokens(
            f"{packet.get('non_compliant_narrative', '')} {packet.get('compliant_narrative', '')}",
            limit=12,
        )
    code_tokens = list(packet.get("code_symbols") or []) or _tokens(
        f"{packet.get('non_compliant_code', '')} {packet.get('compliant_code', '')}",
        limit=12,
    )
    claim_tokens = list(field_terms.get("claim") or []) or _tokens(
        " ".join(list(packet.get("claim_phrases") or [])),
        limit=12,
    )

    variants: list[dict[str, str]] = []
    if title_tokens:
        variants.append({"name": "title_focus", "query": " ".join(title_tokens[:8])})
    if rationale_tokens:
        variants.append({"name": "rationale_focus", "query": " ".join(rationale_tokens[:10])})
    if code_tokens:
        variants.append({"name": "unsafe_code_focus", "query": " ".join(code_tokens[:10])})
    if claim_tokens:
        variants.append({"name": "claim_focus", "query": " ".join(claim_tokens[:10])})
    hybrid_seed = title_tokens[:4] + rationale_tokens[:4] + narrative_tokens[:3] + code_tokens[:3]
    hybrid: list[str] = []
    for token in hybrid_seed:
        if token not in hybrid:
            hybrid.append(token)
        if len(hybrid) >= 12:
            break
    if hybrid:
        variants.append({"name": "hybrid_focus", "query": " ".join(hybrid)})
    if not variants:
        variants.append({"name": "fallback", "query": str(packet.get("title", "")).strip()})
    return variants


def gather_candidates(
    *,
    packet: dict[str, Any],
    db_path: Path | None = None,
    limit_per_variant: int = 10,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    variants = build_query_variants(packet)
    rows: list[dict[str, Any]] = []
    for variant in variants:
        name = str(variant.get("name", "")).strip()
        query = str(variant.get("query", "")).strip()
        if not query:
            continue
        for row in search_fls_paragraphs(query, db_path=db_path, limit=limit_per_variant):
            rows.append(
                {
                    **row,
                    "variant_name": name,
                    "variant_query": query,
                }
            )
    return rows, variants
