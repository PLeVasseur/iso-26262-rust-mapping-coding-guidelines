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


def _normalize_term_mapping(raw_mapping: dict[str, Any]) -> dict[str, list[str]]:
    return {
        str(token).strip().lower(): [
            str(term).strip().lower() for term in list(values or []) if str(term).strip()
        ]
        for token, values in raw_mapping.items()
        if str(token).strip()
    }


def rewrite_query(
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
    suppress_tokens_raw = rules.get("suppress_tokens_when_present") or {}
    allow_row_marker_terms = bool(rules.get("allow_row_marker_terms", True))

    token_expansions = _normalize_term_mapping(token_expansions_raw)
    row_terms = _normalize_term_mapping(row_terms_raw)
    mode_terms = _normalize_term_mapping(mode_terms_raw)
    suppress_tokens = {
        str(token).strip().lower(): {
            str(trigger).strip().lower() for trigger in list(values or []) if str(trigger).strip()
        }
        for token, values in suppress_tokens_raw.items()
        if str(token).strip()
    }

    tokens_in_order = [match.group(0).lower() for match in TOKEN_RE.finditer(original.lower())]
    base_seen = set(tokens_in_order)
    suppressed_tokens = {
        token
        for token in tokens_in_order
        if token in suppress_tokens and suppress_tokens[token].intersection(base_seen)
    }
    filtered_tokens = [token for token in tokens_in_order if token not in suppressed_tokens]
    seen = set(filtered_tokens)
    added_terms: list[str] = []
    strategy_tags = [strategy]
    if suppressed_tokens:
        strategy_tags.append("token-suppression")

    for token in filtered_tokens:
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
    normalized_mode_name = str(mode).strip().lower()
    for term in mode_terms.get(normalized_mode_name, []):
        if term in seen:
            continue
        seen.add(term)
        added_terms.append(term)
        mode_terms_added = True
    if mode_terms_added:
        strategy_tags.append("mode-terms")

    mode_specific_rules_raw = rules.get("mode_specific_rules") or {}
    if not isinstance(mode_specific_rules_raw, dict):
        raise ValueError("mode_specific_rules must be a mapping")
    scoped_rules = mode_specific_rules_raw.get(normalized_mode_name) or {}
    if scoped_rules and not isinstance(scoped_rules, dict):
        raise ValueError(f"mode_specific_rules.{normalized_mode_name} must be a mapping")

    mode_token_expansions = _normalize_term_mapping(scoped_rules.get("token_expansions") or {})
    scoped_added = False
    for token in filtered_tokens:
        for term in mode_token_expansions.get(token, []):
            if term in seen:
                continue
            seen.add(term)
            added_terms.append(term)
            scoped_added = True
    if scoped_added:
        strategy_tags.append("mode-specific-token-expansion")

    rewritten = " ".join(filtered_tokens + added_terms).strip() or original
    semantic_intent_prefix = str(scoped_rules.get("semantic_intent_prefix", "")).strip().lower()
    if semantic_intent_prefix and normalized_mode_name in {"semantic", "hybrid"}:
        rewritten = f"{semantic_intent_prefix} {rewritten}".strip()
        strategy_tags.append("semantic-intent-prefix")

    return {
        "enabled": True,
        "strategy_tags": strategy_tags,
        "rules_path": str(rewrite_rules_path),
        "original_query": original,
        "rewritten_query": rewritten,
        "added_terms": added_terms,
    }


def rewrite_query_text(
    *,
    query_text: str,
    row_marker: str,
    mode: str,
    rewrite_mode: str,
    rewrite_rules_path: Path,
) -> dict[str, Any]:
    return rewrite_query(
        query_text=query_text,
        row_marker=row_marker,
        mode=mode,
        rewrite_mode=rewrite_mode,
        rewrite_rules_path=rewrite_rules_path,
    )
