from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from retrieval.query.row_markers import (
    annotate_rows_with_row_markers,
    filter_rows_by_row_marker,
)
from retrieval.query.embedding_cache import sha256_text
from retrieval.query.semantic_math import min_max_normalize
from retrieval.query.text_processing import split_csv_field, tokenize, tokenize_raw
from sqlite_query_guardrails import GuardrailError, execute_contract_query


class RetrievalContractProfileLike(Protocol):
    @property
    def lexical_query_id(self) -> str: ...

    @property
    def lexical_id_column(self) -> str: ...

    @property
    def corpus_query_id(self) -> str: ...

    @property
    def corpus_cursor_param(self) -> str: ...

    @property
    def row_requirements_query_id(self) -> str: ...


def to_fts_query(query_text: str) -> str:
    tokens = tokenize(query_text)
    if not tokens:
        tokens = tokenize_raw(query_text)
    ordered = sorted(tokens)
    if not ordered:
        raise GuardrailError("Query text did not yield searchable lexical tokens")
    return " OR ".join(ordered)


def materialize_common_row(raw_row: dict[str, Any], query_tokens: set[str]) -> dict[str, Any]:
    statement_id = str(raw_row.get("statement_id", raw_row.get("chunk_uid", "")))
    text = str(raw_row.get("statement_text", raw_row.get("chunk_text", "")))
    overlap = len(query_tokens.intersection(tokenize(text))) if query_tokens else 0
    bm25_raw = float(raw_row.get("bm25_raw", 0.0))
    payload = dict(raw_row)
    payload.update(
        {
            "statement_id": statement_id,
            "statement_text": text,
            "section_heading": str(raw_row.get("section_heading", "")),
            "source_anchor": str(raw_row.get("source_anchor", "")),
            "source_fetched_at": str(raw_row.get("source_fetched_at", "")),
            "row_markers": split_csv_field(str(raw_row.get("row_markers", ""))),
            "mechanism_ids": split_csv_field(str(raw_row.get("mechanism_ids", ""))),
            "mechanism_families": split_csv_field(str(raw_row.get("mechanism_families", ""))),
            "text_sha256": sha256_text(text.lower()),
            "bm25_raw": bm25_raw,
            "phrase_match": int(raw_row.get("phrase_match", 0) or 0),
            "token_overlap_count": overlap,
            "lexical_score": -bm25_raw,
        }
    )
    if "chunk_uid" in raw_row or "chunk_text" in raw_row:
        payload["chunk_uid"] = statement_id
        payload["chunk_text"] = text
    return payload


def run_lexical_query(
    *,
    contract_path: Path,
    retrieval_contract: RetrievalContractProfileLike,
    query_log_root: Path,
    db_path: Path,
    corpus_rows: list[dict[str, Any]],
    row_profiles: list[dict[str, Any]],
    query_text: str,
    row_marker: str,
    row_limit: int,
    row_identity: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    query_tokens = tokenize(query_text)
    fts_query = to_fts_query(query_text)

    corpus_by_statement_id = {str(row["statement_id"]): row for row in corpus_rows}
    result = execute_contract_query(
        db_path=db_path,
        contract_path=contract_path,
        query_id=retrieval_contract.lexical_query_id,
        params={"fts_query": fts_query},
        row_limit=row_limit,
        query_log_root=query_log_root,
    )

    if not result["rows"]:
        fallback_tokens = sorted(tokenize_raw(query_text))
        fallback_query = " OR ".join(fallback_tokens)
        if fallback_query and fallback_query != fts_query:
            result = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id=retrieval_contract.lexical_query_id,
                params={"fts_query": fallback_query},
                row_limit=row_limit,
                query_log_root=query_log_root,
            )

    rows: list[dict[str, Any]] = []
    for match_row in result["rows"]:
        statement_id = str(
            match_row.get(
                retrieval_contract.lexical_id_column,
                match_row.get("statement_id", ""),
            )
        )
        if statement_id not in corpus_by_statement_id:
            continue

        row = dict(corpus_by_statement_id[statement_id])
        row["bm25_raw"] = float(match_row.get("bm25_raw", 0.0))
        row["phrase_match"] = int(query_text.lower() in str(row["statement_text"]).lower())
        row["token_overlap_count"] = len(
            query_tokens.intersection(tokenize(str(row["statement_text"])))
        )
        rows.append(row)

    if not rows:
        return []

    if query_tokens:
        max_overlap = max(int(row["token_overlap_count"]) for row in rows)
        target_overlap = max(1, int(math.ceil(len(query_tokens) * 0.6)))
        min_overlap_required = min(max_overlap, target_overlap)
        filtered_rows = [
            row for row in rows if int(row["token_overlap_count"]) >= min_overlap_required
        ]
        if filtered_rows:
            rows = filtered_rows

    bm25_values = [-float(row["bm25_raw"]) for row in rows]
    overlap_values = [float(row["token_overlap_count"]) for row in rows]
    bm25_norm = min_max_normalize(bm25_values)
    overlap_norm = min_max_normalize(overlap_values)

    for row, bm25_score, overlap_score in zip(rows, bm25_norm, overlap_norm, strict=False):
        row["lexical_score"] = (
            (0.55 * float(bm25_score))
            + (0.35 * float(overlap_score))
            + (0.10 * float(row["phrase_match"]))
        )

    rows.sort(
        key=lambda row: (
            -float(row["lexical_score"]),
            float(row["bm25_raw"]),
            row_identity(row),
        )
    )
    annotate_rows_with_row_markers(rows, row_profiles)
    return filter_rows_by_row_marker(rows, row_marker)


def load_statement_corpus(
    *,
    db_path: Path,
    contract_path: Path,
    query_log_root: Path,
    retrieval_contract: RetrievalContractProfileLike,
    full_corpus_page_limit: int,
    max_rows: int | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    statement_id_after = ""

    while True:
        result = execute_contract_query(
            db_path=db_path,
            contract_path=contract_path,
            query_id=retrieval_contract.corpus_query_id,
            params={retrieval_contract.corpus_cursor_param: statement_id_after},
            row_limit=full_corpus_page_limit,
            query_log_root=query_log_root,
        )
        batch = [materialize_common_row(row, set()) for row in result["rows"]]
        if not batch:
            break

        rows.extend(batch)
        if max_rows is not None and len(rows) >= int(max_rows):
            return rows[: int(max_rows)]

        if len(batch) < full_corpus_page_limit:
            break
        statement_id_after = str(batch[-1]["statement_id"])

    return rows


def load_table1_row_requirements(
    *,
    db_path: Path,
    contract_path: Path,
    query_log_root: Path,
    retrieval_contract: RetrievalContractProfileLike,
) -> list[dict[str, Any]]:
    result = execute_contract_query(
        db_path=db_path,
        contract_path=contract_path,
        query_id=retrieval_contract.row_requirements_query_id,
        params={"row_marker": ""},
        row_limit=20,
        query_log_root=query_log_root,
    )

    profiles: list[dict[str, Any]] = []
    for row in result["rows"]:
        row_marker = str(row.get("row_marker", "")).strip().lower()
        requirement_text = str(row.get("requirement_text", "")).strip()
        profile_terms = split_csv_field(str(row.get("profile_terms", "")))
        if not row_marker:
            continue

        tokens = tokenize(requirement_text)
        for term in profile_terms:
            tokens.update(tokenize(term))
        if not tokens:
            tokens = tokenize_raw(requirement_text)
        if not tokens:
            for term in profile_terms:
                tokens.update(tokenize_raw(term))
        profiles.append(
            {
                "row_marker": row_marker,
                "requirement_text": requirement_text,
                "profile_terms": profile_terms,
                "tokens": tokens,
            }
        )

    profiles.sort(key=lambda value: str(value["row_marker"]))
    return profiles
