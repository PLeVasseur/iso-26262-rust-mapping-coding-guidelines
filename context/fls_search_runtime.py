from __future__ import annotations

from pathlib import Path
from typing import Any

from context.fls_lookup import (
    _bootstrap_scripts_path,
    _load_fls_runtime_settings,
    _resolve_fls_db_path,
)


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
    resolved_db_path = _resolve_fls_db_path(db_path)
    if not resolved_db_path.exists():
        return []
    _bootstrap_scripts_path()
    from retrieval.operations.query import execute_retrieval_query
    from semantic_backend_client import SemanticBackendConfig

    runtime = _load_fls_runtime_settings()
    active_db_path = resolved_db_path if resolved_db_path.exists() else Path(runtime["db_path"])
    top_k = max(int(limit), int(runtime["top_k"]))
    candidate_limit = max(int(runtime["candidate_limit"]), top_k * 8)
    semantic_config = SemanticBackendConfig(
        base_url=str(runtime["semantic_base_url"]),
        embed_model_id=str(runtime["embed_model_id"]),
        reranker_model_id=str(runtime["reranker_model_id"]),
        timeout_sec=float(runtime["semantic_timeout_sec"]),
        embed_base_url=str(runtime["semantic_embed_base_url"]),
        rerank_base_url=str(runtime["semantic_rerank_base_url"]),
    )
    merged: dict[str, dict[str, Any]] = {}
    for mode in ("lexical", "semantic", "hybrid"):
        result = execute_retrieval_query(
            mode=mode,
            db_path=active_db_path,
            contract_path=Path(runtime["contract_path"]),
            query_log_root=Path(runtime["query_log_root"]),
            query_text=query,
            row_marker="",
            top_k=top_k,
            candidate_limit=candidate_limit,
            allow_degraded=False,
            semantic_config=semantic_config,
            semantic_retries=int(runtime["semantic_retries"]),
            persist_semantic_cache=False,
            allow_online_corpus_embedding=False,
            corpus="fls_spec",
            rewrite_rules_path=Path(runtime["rewrite_rules_path"]),
            hybrid_fusion_method=str(runtime["hybrid_fusion_method"]),
            hybrid_rrf_k=int(runtime["hybrid_rrf_k"]),
            hybrid_rrf_window=int(runtime["hybrid_rrf_window"]),
            hybrid_lexical_floor_count=int(runtime["hybrid_lexical_floor_count"]),
            hybrid_lexical_floor_share=float(runtime["hybrid_lexical_floor_share"]),
            hybrid_candidate_policy=str(runtime["hybrid_candidate_policy"]),
            hybrid_rerank_pool_size=int(runtime["hybrid_rerank_pool_size"]),
            hybrid_lexical_min=int(runtime["hybrid_lexical_min"]),
            hybrid_semantic_min=int(runtime["hybrid_semantic_min"]),
        )
        for row in list(result.get("rows") or [])[:top_k]:
            normalized = _normalize_retrieval_row(
                {
                    **row,
                    "requested_mode": str(result.get("requested_mode", mode)),
                    "executed_mode": str(result.get("executed_mode", mode)),
                }
            )
            paragraph_id = normalized["paragraph_id"]
            if not paragraph_id:
                continue
            existing = merged.get(paragraph_id)
            if existing is None or float(normalized.get("relevance_score", 0.0)) > float(
                existing.get("relevance_score", 0.0)
            ):
                merged_modes = list(existing.get("retrieved_modes", [])) if existing else []
                if mode not in merged_modes:
                    merged_modes.append(mode)
                normalized["retrieved_modes"] = merged_modes
                merged[paragraph_id] = normalized
            elif mode not in list(existing.get("retrieved_modes", [])):
                existing.setdefault("retrieved_modes", []).append(mode)
    rows = list(merged.values())
    rows.sort(
        key=lambda row: (
            -float(row.get("relevance_score", 0.0)),
            -float(row.get("lexical_score", 0.0)),
            str(row.get("paragraph_id", "")),
        )
    )
    return rows[: max(1, int(limit))]
