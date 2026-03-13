from __future__ import annotations

import math
from typing import Any

from retrieval.query.text_processing import tokenize, tokenize_raw


def derive_row_marker_scores(
    statement_text: str,
    row_profiles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    statement_tokens = tokenize(statement_text)
    if not statement_tokens:
        statement_tokens = tokenize_raw(statement_text)
    if not statement_tokens or not row_profiles:
        return []

    matches: list[dict[str, Any]] = []
    for profile in row_profiles:
        row_marker = str(profile.get("row_marker", "")).strip().lower()
        row_tokens = set(profile.get("tokens", set()))
        if not row_marker or not row_tokens:
            continue

        overlap = statement_tokens.intersection(row_tokens)
        overlap_count = len(overlap)
        if overlap_count <= 0:
            continue

        score = float(overlap_count) / math.sqrt(float(len(row_tokens)))
        matches.append(
            {
                "row_marker": row_marker,
                "score": float(score),
                "overlap_count": int(overlap_count),
            }
        )

    if not matches:
        return []

    matches.sort(
        key=lambda row: (
            -float(row["score"]),
            -int(row["overlap_count"]),
            str(row["row_marker"]),
        )
    )

    top_score = float(matches[0]["score"])
    threshold = max(0.20, top_score * 0.72)
    selected = [row for row in matches if float(row["score"]) >= threshold][:3]
    for row in selected:
        row["score"] = round(float(row["score"]), 6)
    return selected


def annotate_rows_with_row_markers(
    rows: list[dict[str, Any]],
    row_profiles: list[dict[str, Any]],
) -> None:
    for row in rows:
        statement_text = str(row.get("statement_text", ""))
        derived = derive_row_marker_scores(statement_text, row_profiles)
        row["row_marker_scores"] = derived
        row["row_markers"] = [str(item["row_marker"]) for item in derived]


def filter_rows_by_row_marker(rows: list[dict[str, Any]], row_marker: str) -> list[dict[str, Any]]:
    scoped = str(row_marker).strip().lower()
    if not scoped:
        return rows
    return [
        row
        for row in rows
        if scoped in {str(marker).strip().lower() for marker in row.get("row_markers", [])}
    ]
