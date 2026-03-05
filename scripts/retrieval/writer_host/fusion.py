from __future__ import annotations

from collections import defaultdict
from typing import Any


def _normalize_scores(rows: list[dict[str, Any]]) -> dict[str, float]:
    values: list[float] = []
    for row in rows:
        score_raw = row.get("score", 0.0) if isinstance(row, dict) else 0.0
        try:
            values.append(float(score_raw))
        except (TypeError, ValueError):
            values.append(0.0)
    if not values:
        return {}
    low = min(values)
    high = max(values)
    span = high - low
    if span <= 0.0:
        return {
            str(row.get("statement_id", "")): 1.0
            for row in rows
            if str(row.get("statement_id", ""))
        }
    normalized: dict[str, float] = {}
    for row in rows:
        statement_id = str(row.get("statement_id", "")).strip()
        if not statement_id:
            continue
        try:
            score = float(row.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        normalized[statement_id] = max(0.0, min(1.0, (score - low) / span))
    return normalized


def fuse_ranked_lists(
    *,
    ranked_rows_by_mode: dict[str, list[dict[str, Any]]],
    rrf_k: int,
    rank_window: int,
    top_n: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fuse multi-mode retrieval rows using RRF with light score tie-breakers."""

    bucket: dict[str, dict[str, Any]] = {}
    mode_score_maps = {
        mode: _normalize_scores(rows)
        for mode, rows in ranked_rows_by_mode.items()
        if isinstance(rows, list)
    }

    for mode, rows in ranked_rows_by_mode.items():
        if not isinstance(rows, list):
            continue
        for rank, row in enumerate(rows[: max(1, int(rank_window))], start=1):
            if not isinstance(row, dict):
                continue
            statement_id = str(row.get("statement_id", "")).strip()
            if not statement_id:
                continue
            entry = bucket.setdefault(
                statement_id,
                {
                    "statement_id": statement_id,
                    "source_anchor": str(row.get("source_anchor", "")).strip(),
                    "doc_id": str(row.get("doc_id", "")).strip(),
                    "statement_text": str(row.get("statement_text", "")).strip(),
                    "rrf_score": 0.0,
                    "best_mode_score": 0.0,
                    "best_rank": rank,
                    "mode_hits": set(),
                    "mode_ranks": {},
                    "mode_scores": {},
                },
            )
            entry["rrf_score"] += 1.0 / float(int(rrf_k) + rank)
            entry["best_rank"] = min(int(entry.get("best_rank", rank) or rank), rank)
            entry["mode_hits"].add(mode)
            entry["mode_ranks"][mode] = rank
            mode_score = mode_score_maps.get(mode, {}).get(statement_id, 0.0)
            entry["mode_scores"][mode] = mode_score
            entry["best_mode_score"] = max(float(entry.get("best_mode_score", 0.0)), mode_score)
            if not str(entry.get("source_anchor", "")).strip():
                entry["source_anchor"] = str(row.get("source_anchor", "")).strip()
            if not str(entry.get("doc_id", "")).strip():
                entry["doc_id"] = str(row.get("doc_id", "")).strip()
            if not str(entry.get("statement_text", "")).strip():
                entry["statement_text"] = str(row.get("statement_text", "")).strip()

    scored: list[dict[str, Any]] = []
    for entry in bucket.values():
        coverage = len(entry.get("mode_hits", set()))
        coverage_bonus = {1: 0.0, 2: 0.1, 3: 0.2}.get(coverage, 0.0)
        score = (
            float(entry.get("rrf_score", 0.0))
            + 0.2 * float(entry.get("best_mode_score", 0.0))
            + coverage_bonus
        )
        rows = dict(entry)
        rows["coverage"] = coverage
        rows["fused_score"] = score
        rows["mode_hits"] = sorted(str(mode) for mode in entry.get("mode_hits", set()))
        scored.append(rows)

    scored.sort(
        key=lambda row: (
            -float(row.get("fused_score", 0.0)),
            -int(row.get("coverage", 0) or 0),
            int(row.get("best_rank", 10**9) or 10**9),
            str(row.get("statement_id", "")),
        )
    )

    selected: list[dict[str, Any]] = []
    per_anchor: dict[str, int] = defaultdict(int)
    for row in scored:
        anchor = str(row.get("source_anchor", "")).strip()
        if anchor and per_anchor[anchor] >= 2:
            continue
        selected.append(row)
        if anchor:
            per_anchor[anchor] += 1
        if len(selected) >= max(1, int(top_n)):
            break

    decision = {
        "candidate_count": len(scored),
        "selected_count": len(selected),
        "rrf_k": int(rrf_k),
        "rank_window": int(rank_window),
        "top_n": int(top_n),
        "selection_policy": "rrf_plus_score_with_anchor_cap",
    }
    return selected, decision
