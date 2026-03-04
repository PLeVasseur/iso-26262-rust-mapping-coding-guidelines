from __future__ import annotations

from typing import Any


def apply_corpus_row_policy(
    rows: list[dict[str, Any]], *, query_text: str, corpus: str
) -> list[dict[str, Any]]:
    normalized_corpus = str(corpus).strip().lower()
    if normalized_corpus == "core_docs":
        from retrieval.query_policies.core_docs import apply_target_hint_preference

        return apply_target_hint_preference(rows, query_text=query_text)
    if normalized_corpus == "rust_reference":
        from retrieval.query_policies.rust_reference import apply_intent_path_preference

        return apply_intent_path_preference(rows, query_text=query_text)
    return rows


def without_score_breakdown(result: dict[str, Any], *, score_fields: set[str]) -> dict[str, Any]:
    projected = dict(result)
    projected.pop("row_projection", None)
    projected.pop("row_projection_all", None)

    rows = []
    for row in result.get("rows", []):
        if not isinstance(row, dict):
            continue
        rows.append({key: value for key, value in row.items() if key not in score_fields})
    projected["rows"] = rows
    return projected
