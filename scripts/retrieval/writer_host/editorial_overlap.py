from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_:#\-]*")
_STOPWORDS = {
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
}


def _tokens(*values: Any) -> set[str]:
    out: set[str] = set()
    for value in values:
        text = str(value or "").lower()
        for token in _TOKEN_RE.findall(text):
            if len(token) < 4 or token in _STOPWORDS:
                continue
            out.add(token)
    return out


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / float(len(left | right))


def analyze_overlap(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: list[dict[str, Any]] = []
    by_target: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        target_id = str(row.get("target_id", "")).strip()
        if target_id:
            by_target[target_id] = [row]
    items = list(rows)
    for index, left in enumerate(items):
        for right in items[index + 1 :]:
            left_tokens = _tokens(
                left.get("title"),
                left.get("chapter"),
                left.get("construct_terms"),
                left.get("claim_text_blob"),
            )
            right_tokens = _tokens(
                right.get("title"),
                right.get("chapter"),
                right.get("construct_terms"),
                right.get("claim_text_blob"),
            )
            score = _jaccard(left_tokens, right_tokens)
            if score < 0.4:
                continue
            pairs.append(
                {
                    "left_target_id": str(left.get("target_id", "")),
                    "right_target_id": str(right.get("target_id", "")),
                    "score": round(score, 4),
                    "kind": "near_duplicate" if score >= 0.6 else "family_overlap",
                }
            )
    return {
        "status": "pass" if not pairs else "review",
        "pair_count": len(pairs),
        "pairs": sorted(pairs, key=lambda item: float(item.get("score", 0.0)), reverse=True),
    }
