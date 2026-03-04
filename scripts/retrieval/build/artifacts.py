from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from retrieval.build.mechanisms import extract_mechanisms_and_evidence
from retrieval.build.queryability import (
    build_row_queryability,
    build_semantic_corpus,
    build_semantic_models,
)
from retrieval.build.reference_parsing import SectionRecord, StatementRecord
from retrieval.build.table1_rows import resolve_table1_rows
from retrieval.ingest.contracts import ChunkInput


@dataclass(frozen=True)
class ChunkRecord:
    chunk_uid: str
    section_id: str
    raw_text: str
    clean_text: str
    char_len: int
    token_len: int
    source_sha256: str
    source_fetched_at: str
    source_commit_sha: str
    order_index: int


@dataclass(frozen=True)
class ChunkSpanRecord:
    chunk_uid: str
    source_anchor: str
    start_offset: int
    end_offset: int
    span_order: int


def build_retrieval_artifacts(
    *,
    strategy: Any,
    sections: list[SectionRecord],
    statements: list[StatementRecord],
    source_fetched_at: str,
    extractor_db: Path,
    table_node_id: str,
    reference_source_url: str,
    retrieval_mode: str,
    semantic_profile_version: str,
    embedding_model_id: str,
    embedding_model_revision: str,
    embedding_model_license: str,
    embedding_dim: int,
    reranker_model_id: str,
    reranker_model_revision: str,
    reranker_model_license: str,
    chunk_target_min_tokens: int,
    chunk_target_max_tokens: int,
    chunk_overlap_percent: float,
) -> dict[str, Any]:
    chunk_result = strategy.build_chunks(
        ChunkInput(
            sections=sections,
            target_min_tokens=int(chunk_target_min_tokens),
            target_max_tokens=int(chunk_target_max_tokens),
            overlap_percent=float(chunk_overlap_percent),
        )
    )
    chunks = [
        ChunkRecord(
            chunk_uid=str(row["chunk_uid"]),
            section_id=str(row["section_id"]),
            raw_text=str(row["raw_text"]),
            clean_text=str(row["clean_text"]),
            char_len=int(row["char_len"]),
            token_len=int(row["token_len"]),
            source_sha256=str(row["source_sha256"]),
            source_fetched_at=str(row["source_fetched_at"]),
            source_commit_sha=str(row["source_commit_sha"]),
            order_index=int(row["order_index"]),
        )
        for row in chunk_result.chunks
    ]
    chunk_spans = [
        ChunkSpanRecord(
            chunk_uid=str(row["chunk_uid"]),
            source_anchor=str(row["source_anchor"]),
            start_offset=int(row["start_offset"]),
            end_offset=int(row["end_offset"]),
            span_order=int(row["span_order"]),
        )
        for row in chunk_result.spans
    ]

    mechanisms, mechanism_evidence, evidence_count_by_mechanism, best_anchor_by_mechanism = (
        extract_mechanisms_and_evidence(
            sections=sections,
            statements=statements,
            source_fetched_at=source_fetched_at,
            source_url=reference_source_url,
        )
    )
    semantic_models = build_semantic_models(
        source_fetched_at=source_fetched_at,
        retrieval_mode=retrieval_mode,
        embedding_model_id=embedding_model_id,
        embedding_model_revision=embedding_model_revision,
        embedding_model_license=embedding_model_license,
        embedding_dim=embedding_dim,
        reranker_model_id=reranker_model_id,
        reranker_model_revision=reranker_model_revision,
        reranker_model_license=reranker_model_license,
    )

    table_rows = resolve_table1_rows(extractor_db=extractor_db, table_node_id=table_node_id)
    semantic_corpus = build_semantic_corpus(
        table_rows=table_rows,
        mechanisms=mechanisms,
        mechanism_evidence=mechanism_evidence,
        statements=statements,
        source_fetched_at=source_fetched_at,
        reference_source_url=reference_source_url,
    )

    row_verdicts, row_mechanisms, row_mechanism_scores, counts = build_row_queryability(
        table_rows=table_rows,
        mechanisms=mechanisms,
        mechanism_evidence=mechanism_evidence,
        statements=statements,
        evidence_count_by_mechanism=evidence_count_by_mechanism,
        best_anchor_by_mechanism=best_anchor_by_mechanism,
        source_fetched_at=source_fetched_at,
        retrieval_mode=retrieval_mode,
        semantic_profile_version=semantic_profile_version,
        reference_source_url=reference_source_url,
    )

    return {
        "chunks": chunks,
        "chunk_spans": chunk_spans,
        "mechanisms": mechanisms,
        "mechanism_evidence": mechanism_evidence,
        "semantic_models": semantic_models,
        "table_rows": table_rows,
        "semantic_corpus": semantic_corpus,
        "row_verdicts": row_verdicts,
        "row_mechanisms": row_mechanisms,
        "row_mechanism_scores": row_mechanism_scores,
        "counts": counts,
    }
