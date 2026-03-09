from __future__ import annotations

from pathlib import Path
from typing import Any

from context.fls_lookup import _resolve_fls_db_path


def _normalize_retrieval_row(row: dict[str, Any]) -> dict[str, Any]:
    paragraph_id = str(
        row.get("paragraph_id") or row.get("statement_id") or row.get("chunk_uid") or ""
    ).strip()
    return {
        "paragraph_id": paragraph_id,
        "paragraph_number": str(row.get("paragraph_number", "")),
        "chapter": str(row.get("chapter", "")),
        "section": str(row.get("section", row.get("section_heading", ""))),
        "text": str(row.get("text") or row.get("statement_text") or row.get("chunk_text") or ""),
        "source_file": str(row.get("source_file", "")),
        "document_link": str(row.get("document_link", "")),
        "section_link": str(row.get("section_link", "")),
        "paragraph_link": str(row.get("paragraph_link", "")),
        "section_id": str(row.get("section_id", "")),
        "checksum": str(row.get("checksum", "")),
        "chunk_uid": str(row.get("chunk_uid", paragraph_id)),
        "source_anchor": str(row.get("source_anchor", "")),
        "requested_mode": str(row.get("requested_mode", "")),
        "executed_mode": str(row.get("executed_mode", "")),
        "bm25_rank": float(row.get("bm25_rank", row.get("bm25_raw", 0.0)) or 0.0),
        "lexical_score": float(row.get("lexical_score", 0.0) or 0.0),
        "semantic_score": float(row.get("semantic_score", 0.0) or 0.0),
        "reranker_score": float(row.get("reranker_score", 0.0) or 0.0),
        "relevance_score": float(
            row.get("relevance_score", row.get("semantic_score", row.get("lexical_score", 0.0)))
            or 0.0
        ),
    }


def search_fls_paragraphs(
    query: str,
    *,
    db_path: Path | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    raise RuntimeError(
        "WS7 ranking boundary requires direct execute_retrieval_query(...) rows; "
        "search_fls_paragraphs(...) legacy compatibility helper is retired"
    )
