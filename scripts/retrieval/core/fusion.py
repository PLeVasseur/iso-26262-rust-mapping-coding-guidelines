from __future__ import annotations

from collections.abc import Callable
from typing import Any


def apply_component_scores(
    row: dict[str, Any],
    *,
    lexical_score: float,
    semantic_score: float,
    reranker_score: float,
    lexical_weight: float,
    semantic_weight: float,
    reranker_weight: float,
) -> None:
    final_score = (
        (float(lexical_weight) * float(lexical_score))
        + (float(semantic_weight) * float(semantic_score))
        + (float(reranker_weight) * float(reranker_score))
    )
    row["lexical_score"] = float(lexical_score)
    row["semantic_score"] = float(semantic_score)
    row["reranker_score"] = float(reranker_score)
    row["final_score"] = float(final_score)
    row["relevance_score"] = float(final_score)


def build_rank_map(
    rows: list[dict[str, Any]],
    *,
    window: int,
    row_identity: Callable[[dict[str, Any]], str],
) -> dict[str, int]:
    rank_map: dict[str, int] = {}
    if window <= 0:
        return rank_map
    for rank, row in enumerate(rows[:window], start=1):
        row_id = row_identity(row)
        if not row_id or row_id in rank_map:
            continue
        rank_map[row_id] = int(rank)
    return rank_map


def apply_rrf_hybrid_scores(
    *,
    merged_rows: dict[str, dict[str, Any]],
    lexical_rows: list[dict[str, Any]],
    semantic_rows: list[dict[str, Any]],
    rrf_k: int,
    rrf_window: int,
    row_identity: Callable[[dict[str, Any]], str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lexical_rank_map = build_rank_map(lexical_rows, window=rrf_window, row_identity=row_identity)
    semantic_rank_map = build_rank_map(semantic_rows, window=rrf_window, row_identity=row_identity)

    reranker_ranked = sorted(
        semantic_rows,
        key=lambda row: (
            -float(row.get("reranker_score", 0.0)),
            -float(row.get("semantic_score", 0.0)),
            row_identity(row),
        ),
    )
    reranker_rank_map = build_rank_map(
        reranker_ranked, window=rrf_window, row_identity=row_identity
    )

    contribution_counts = {"0": 0, "1": 0, "2": 0, "3": 0}
    hybrid_rows: list[dict[str, Any]] = []
    rank_constant = max(1, int(rrf_k))

    for row in merged_rows.values():
        row_id = row_identity(row)
        lexical_rank = lexical_rank_map.get(row_id, 0)
        semantic_rank = semantic_rank_map.get(row_id, 0)
        reranker_rank = reranker_rank_map.get(row_id, 0)

        contribution_count = (
            int(bool(lexical_rank)) + int(bool(semantic_rank)) + int(bool(reranker_rank))
        )
        contribution_counts[str(contribution_count)] += 1

        rrf_score = 0.0
        if lexical_rank:
            rrf_score += 1.0 / float(rank_constant + lexical_rank)
        if semantic_rank:
            rrf_score += 1.0 / float(rank_constant + semantic_rank)
        if reranker_rank:
            rrf_score += 1.0 / float(rank_constant + reranker_rank)

        row["lexical_rank"] = int(lexical_rank)
        row["semantic_rank"] = int(semantic_rank)
        row["reranker_rank"] = int(reranker_rank)
        row["rrf_score"] = float(rrf_score)
        row["final_score"] = float(rrf_score)
        row["relevance_score"] = float(rrf_score)
        hybrid_rows.append(row)

    hybrid_rows.sort(
        key=lambda row: (
            -float(row.get("rrf_score", 0.0)),
            int(row.get("reranker_rank", 0)) or 10**9,
            int(row.get("lexical_rank", 0)) or 10**9,
            row_identity(row),
        )
    )

    return hybrid_rows, {
        "lists_fused": ["lexical", "semantic", "reranker"],
        "contribution_counts": contribution_counts,
    }
