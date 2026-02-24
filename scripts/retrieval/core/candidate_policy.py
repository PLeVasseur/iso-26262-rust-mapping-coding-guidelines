from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from retrieval.core.profile import HYBRID_CANDIDATE_POLICY_V2


def compose_hybrid_rerank_pool_v2(
    *,
    lexical_rows: list[dict[str, Any]],
    semantic_rows: list[dict[str, Any]],
    rerank_pool_size: int,
    lexical_min: int,
    semantic_min: int,
    row_identity: Callable[[dict[str, Any]], str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    target = max(1, int(rerank_pool_size))
    resolved_lexical_min = max(0, min(int(lexical_min), target))
    resolved_semantic_min = max(0, min(int(semantic_min), target))

    pool: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _append_rows(rows: list[dict[str, Any]], limit: int) -> int:
        added = 0
        for row in rows:
            row_id = row_identity(row)
            if not row_id or row_id in seen_ids:
                continue
            seen_ids.add(row_id)
            pool.append(row)
            added += 1
            if added >= limit or len(pool) >= target:
                break
        return added

    lexical_added = _append_rows(lexical_rows, resolved_lexical_min)
    semantic_added = _append_rows(semantic_rows, resolved_semantic_min)

    overlap_rows: list[dict[str, Any]] = []
    lexical_ids = {row_identity(row) for row in lexical_rows}
    for row in semantic_rows:
        row_id = row_identity(row)
        if row_id and row_id in lexical_ids:
            overlap_rows.append(row)
    overlap_added = _append_rows(overlap_rows, target)

    _append_rows(semantic_rows, target)
    _append_rows(lexical_rows, target)

    return pool[:target], {
        "policy": HYBRID_CANDIDATE_POLICY_V2,
        "rerank_pool_target": int(target),
        "lexical_min": int(resolved_lexical_min),
        "semantic_min": int(resolved_semantic_min),
        "lexical_added": int(lexical_added),
        "semantic_added": int(semantic_added),
        "overlap_added": int(overlap_added),
        "final_pool_size": int(len(pool[:target])),
    }


def apply_hybrid_candidate_policy_v2_rerank(
    *,
    merged_rows: dict[str, dict[str, Any]],
    lexical_rows: list[dict[str, Any]],
    semantic_rows: list[dict[str, Any]],
    query_text: str,
    top_k: int,
    rerank_pool_size: int,
    lexical_min: int,
    semantic_min: int,
    row_identity: Callable[[dict[str, Any]], str],
    rerank_documents: Callable[[str, list[str]], list[float]],
    normalize_scores: Callable[[list[float]], list[float]],
    timing: dict[str, float],
    workload: dict[str, int],
) -> dict[str, Any]:
    resolved_pool_size = max(max(int(top_k) * 8, 64), int(rerank_pool_size))
    rerank_pool, debug = compose_hybrid_rerank_pool_v2(
        lexical_rows=lexical_rows,
        semantic_rows=semantic_rows,
        rerank_pool_size=resolved_pool_size,
        lexical_min=lexical_min,
        semantic_min=semantic_min,
        row_identity=row_identity,
    )
    if not rerank_pool:
        debug["enabled"] = False
        return debug

    rerank_inputs = [str(row.get("statement_text", "")) for row in rerank_pool]
    rerank_started = time.perf_counter()
    reranker_scores_raw = rerank_documents(query_text, rerank_inputs)
    timing["rerank_ms"] = timing.get("rerank_ms", 0.0) + (
        (time.perf_counter() - rerank_started) * 1000.0
    )

    reranker_scores = normalize_scores([float(value) for value in reranker_scores_raw])
    for row, score in zip(rerank_pool, reranker_scores, strict=False):
        row_id = row_identity(row)
        if not row_id or row_id not in merged_rows:
            continue
        merged_rows[row_id]["reranker_score"] = float(score)

    workload["rerank_pool_size"] = max(
        int(workload.get("rerank_pool_size", 0)), int(len(rerank_pool))
    )
    workload["rerank_doc_count"] = max(
        int(workload.get("rerank_doc_count", 0)), int(len(rerank_pool))
    )
    debug["enabled"] = True
    return debug


def apply_hybrid_lexical_floor_rerank(
    *,
    merged_rows: dict[str, dict[str, Any]],
    lexical_rows: list[dict[str, Any]],
    semantic_rows: list[dict[str, Any]],
    query_text: str,
    top_k: int,
    floor_count: int,
    floor_share: float,
    row_identity: Callable[[dict[str, Any]], str],
    rerank_documents: Callable[[str, list[str]], list[float]],
    normalize_scores: Callable[[list[float]], list[float]],
    timing: dict[str, float],
    workload: dict[str, int],
) -> dict[str, Any]:
    rerank_window = max(int(top_k) * 8, 64)
    resolved_share = max(0.0, min(1.0, float(floor_share)))
    resolved_floor_count = max(
        int(floor_count), int(math.ceil(float(rerank_window) * resolved_share))
    )
    if resolved_floor_count <= 0:
        return {
            "enabled": False,
            "resolved_floor_count": 0,
            "lexical_extra_count": 0,
            "rerank_window": int(rerank_window),
        }

    semantic_base = list(semantic_rows[:rerank_window])
    semantic_base_ids = {row_identity(row) for row in semantic_base}

    lexical_extras: list[dict[str, Any]] = []
    for row in lexical_rows:
        row_id = row_identity(row)
        if not row_id or row_id in semantic_base_ids:
            continue
        lexical_extras.append(row)
        if len(lexical_extras) >= resolved_floor_count:
            break

    if not lexical_extras:
        return {
            "enabled": True,
            "resolved_floor_count": int(resolved_floor_count),
            "lexical_extra_count": 0,
            "rerank_window": int(rerank_window),
        }

    combined: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in semantic_base + lexical_extras:
        row_id = row_identity(row)
        if not row_id or row_id in seen_ids:
            continue
        seen_ids.add(row_id)
        combined.append(row)

    rerank_inputs = [str(row.get("statement_text", "")) for row in combined]
    rerank_started = time.perf_counter()
    rerank_scores_raw = rerank_documents(query_text, rerank_inputs)
    timing["rerank_ms"] = timing.get("rerank_ms", 0.0) + (
        (time.perf_counter() - rerank_started) * 1000.0
    )

    rerank_scores = normalize_scores([float(value) for value in rerank_scores_raw])
    for row, score in zip(combined, rerank_scores, strict=False):
        row_id = row_identity(row)
        if not row_id or row_id not in merged_rows:
            continue
        merged_rows[row_id]["reranker_score"] = float(score)

    workload["rerank_pool_size"] = max(int(workload.get("rerank_pool_size", 0)), int(len(combined)))
    workload["rerank_doc_count"] = max(int(workload.get("rerank_doc_count", 0)), int(len(combined)))

    return {
        "enabled": True,
        "resolved_floor_count": int(resolved_floor_count),
        "lexical_extra_count": int(len(lexical_extras)),
        "rerank_window": int(rerank_window),
        "rerank_pool_size": int(len(combined)),
    }
