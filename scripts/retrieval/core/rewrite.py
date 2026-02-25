from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

TOKEN_RE = re.compile(r"[a-z0-9_]+")


def load_rewrite_rules(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError("Rewrite rules payload must be a mapping")
    return payload


def rewrite_query_text(
    *,
    query_text: str,
    row_marker: str,
    mode: str,
    rewrite_mode: str,
    rewrite_rules_path: Path,
) -> dict[str, Any]:
    normalized_mode = str(rewrite_mode).strip().lower() or "auto"
    original = " ".join(str(query_text).split())
    if normalized_mode == "off":
        return {
            "enabled": False,
            "strategy_tags": ["rewrite-disabled"],
            "rules_path": str(rewrite_rules_path),
            "original_query": original,
            "rewritten_query": original,
            "added_terms": [],
        }

    rules = load_rewrite_rules(rewrite_rules_path)
    strategy = str(rules.get("strategy", "rewrite-v1")).strip() or "rewrite-v1"
    token_expansions_raw = rules.get("token_expansions") or {}
    row_terms_raw = rules.get("row_marker_terms") or {}
    mode_terms_raw = rules.get("mode_terms") or {}
    allow_row_marker_terms = bool(rules.get("allow_row_marker_terms", True))

    token_expansions = {
        str(token).strip().lower(): [
            str(term).strip().lower() for term in list(values or []) if str(term).strip()
        ]
        for token, values in token_expansions_raw.items()
        if str(token).strip()
    }
    row_terms = {
        str(marker).strip().lower(): [
            str(term).strip().lower() for term in list(values or []) if str(term).strip()
        ]
        for marker, values in row_terms_raw.items()
        if str(marker).strip()
    }
    mode_terms = {
        str(mode_name).strip().lower(): [
            str(term).strip().lower() for term in list(values or []) if str(term).strip()
        ]
        for mode_name, values in mode_terms_raw.items()
        if str(mode_name).strip()
    }

    tokens_in_order = [match.group(0).lower() for match in TOKEN_RE.finditer(original.lower())]
    seen = set(tokens_in_order)
    added_terms: list[str] = []
    strategy_tags = [strategy]

    for token in tokens_in_order:
        for term in token_expansions.get(token, []):
            if term in seen:
                continue
            seen.add(term)
            added_terms.append(term)
    if added_terms:
        strategy_tags.append("token-expansion")

    scoped_marker = str(row_marker).strip().lower()
    marker_terms_added = False
    if allow_row_marker_terms and scoped_marker in row_terms:
        for term in row_terms[scoped_marker]:
            if term in seen:
                continue
            seen.add(term)
            added_terms.append(term)
            marker_terms_added = True
    if marker_terms_added:
        strategy_tags.append("row-marker-terms")

    mode_terms_added = False
    for term in mode_terms.get(str(mode).strip().lower(), []):
        if term in seen:
            continue
        seen.add(term)
        added_terms.append(term)
        mode_terms_added = True
    if mode_terms_added:
        strategy_tags.append("mode-terms")

    rewritten = " ".join(tokens_in_order + added_terms).strip() or original
    return {
        "enabled": True,
        "strategy_tags": strategy_tags,
        "rules_path": str(rewrite_rules_path),
        "original_query": original,
        "rewritten_query": rewritten,
        "added_terms": added_terms,
    }
