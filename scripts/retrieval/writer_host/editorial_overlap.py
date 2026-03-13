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


def _row_tokens(row: dict[str, Any]) -> set[str]:
    return _tokens(
        row.get("title"),
        row.get("chapter"),
        row.get("construct_terms") or row.get("construct_keywords"),
        row.get("claim_text_blob") or row.get("operative_text"),
        row.get("review_question") or row.get("review_question_hint"),
    )


def top_overlap_candidates(
    row: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    top_k: int = 5,
    candidate_id_key: str = "target_id",
) -> list[dict[str, Any]]:
    left_tokens = _row_tokens(row)
    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        score = _jaccard(left_tokens, _row_tokens(candidate))
        if score <= 0.0:
            continue
        overlap_kind = (
            "near_duplicate"
            if score >= 0.75
            else ("partial_same_family" if score >= 0.45 else "low")
        )
        residue_status = (
            "none"
            if overlap_kind == "near_duplicate"
            else ("weak_residue" if score >= 0.6 else "meaningful_residue")
        )
        ranked.append(
            {
                "candidate_id": str(candidate.get(candidate_id_key, "")).strip(),
                "chapter": str(candidate.get("chapter", "")).strip(),
                "overlap_kind": overlap_kind,
                "overlap_score": round(score, 4),
                "shared_review_question": str(
                    candidate.get("review_question") or candidate.get("review_question_hint") or ""
                ).strip(),
                "shared_constructs": list(
                    candidate.get("construct_terms") or candidate.get("construct_keywords") or []
                )[:6],
                "difference_summary": "Deterministic overlap candidate.",
                "residue_status": residue_status,
            }
        )
    ranked.sort(key=lambda item: float(item.get("overlap_score", 0.0)), reverse=True)
    return ranked[:top_k]


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
                    "left_atom_id": str(left.get("atom_id", "")),
                    "right_atom_id": str(right.get("atom_id", "")),
                    "score": round(score, 4),
                    "kind": "near_duplicate" if score >= 0.6 else "family_overlap",
                }
            )
    return {
        "status": "pass" if not pairs else "review",
        "pair_count": len(pairs),
        "pairs": sorted(pairs, key=lambda item: float(item.get("score", 0.0)), reverse=True),
    }
