from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from retrieval.build.reference_parsing import SectionRecord, SourceDocument, StatementRecord


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compute_snapshot_sha256(
    commit_sha: str,
    documents: list[SourceDocument],
    sections: list[SectionRecord],
    statements: list[StatementRecord],
    chunks: list[Any],
) -> str:
    payload = {
        "commit_sha": commit_sha,
        "document_hashes": sorted((doc.rel_path, doc.source_sha256) for doc in documents),
        "sections": len(sections),
        "statements": len(statements),
        "chunks": len(chunks),
    }
    return _sha256_text(json.dumps(payload, sort_keys=True))


def insert_payload(
    connection: sqlite3.Connection,
    snapshot_id: str,
    commit_sha: str,
    fetched_at: str,
    source_url: str,
    snapshot_sha256: str,
    chapters: list[dict[str, Any]],
    documents: list[SourceDocument],
    sections: list[SectionRecord],
    statements: list[StatementRecord],
    chunks: list[Any],
    chunk_spans: list[Any],
    mechanisms: list[dict[str, Any]],
    mechanism_evidence: list[dict[str, Any]],
    table_rows: list[dict[str, Any]],
    row_verdicts: list[dict[str, Any]],
    row_mechanisms: list[dict[str, Any]],
    semantic_models: list[dict[str, Any]],
    semantic_corpus: list[dict[str, Any]],
    row_mechanism_scores: list[dict[str, Any]],
    extractor_version: str,
    build_notes: str,
) -> None:
    section_heading_by_id = {section.section_id: section.heading for section in sections}

    connection.execute(
        """
        INSERT INTO snapshots(snapshot_id, commit_sha, source_url, fetched_at, sha256)
        VALUES(?, ?, ?, ?, ?)
        """,
        (snapshot_id, commit_sha, source_url, fetched_at, snapshot_sha256),
    )
    connection.execute(
        """
        INSERT INTO kb_metadata(
            kb_id,
            source_name,
            source_revision,
            extractor_version,
            built_at,
            notes
        ) VALUES(?, ?, ?, ?, ?, ?)
        """,
        (
            "rust_reference",
            "rust-reference",
            commit_sha,
            extractor_version,
            fetched_at,
            build_notes,
        ),
    )

    for chapter in chapters:
        connection.execute(
            "INSERT INTO chapters(chapter_id, title, order_index) VALUES(?, ?, ?)",
            (chapter["chapter_id"], chapter["title"], int(chapter["order_index"])),
        )

    for document in documents:
        connection.execute(
            """
            INSERT INTO source_documents(
                document_id,
                snapshot_id,
                chapter_id,
                rel_path,
                title,
                source_sha256,
                source_fetched_at,
                source_commit_sha,
                order_index
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.document_id,
                snapshot_id,
                document.chapter_id,
                document.rel_path,
                document.title,
                document.source_sha256,
                document.source_fetched_at,
                document.source_commit_sha,
                document.doc_order,
            ),
        )
        connection.execute(
            """
            INSERT INTO docs(
                doc_uid,
                source_path,
                title,
                revision,
                fetched_at,
                source_sha256,
                chapter_id,
                order_index
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.document_id,
                document.rel_path,
                document.title,
                document.source_commit_sha,
                document.source_fetched_at,
                document.source_sha256,
                document.chapter_id,
                document.doc_order,
            ),
        )

    for section in sections:
        connection.execute(
            """
            INSERT INTO sections(
                section_id,
                snapshot_id,
                document_id,
                chapter_id,
                anchor,
                heading,
                order_index,
                level,
                text,
                source_sha256,
                source_fetched_at,
                source_commit_sha
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                section.section_id,
                section.snapshot_id,
                section.document_id,
                section.chapter_id,
                section.anchor,
                section.heading,
                section.order_index,
                section.level,
                section.text,
                section.source_sha256,
                section.source_fetched_at,
                section.source_commit_sha,
            ),
        )

    for statement in statements:
        connection.execute(
            """
            INSERT INTO statements(
                statement_id,
                section_id,
                statement_type,
                text,
                confidence,
                sentence_index,
                source_sha256,
                source_fetched_at,
                source_commit_sha
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                statement.statement_id,
                statement.section_id,
                statement.statement_type,
                statement.text,
                statement.confidence,
                statement.sentence_index,
                statement.source_sha256,
                statement.source_fetched_at,
                statement.source_commit_sha,
            ),
        )
        connection.execute(
            """
            INSERT INTO statements_fts(
                statement_id,
                section_id,
                section_heading,
                statement_text
            ) VALUES(?, ?, ?, ?)
            """,
            (
                statement.statement_id,
                statement.section_id,
                section_heading_by_id.get(statement.section_id, ""),
                statement.text,
            ),
        )

    for chunk in chunks:
        connection.execute(
            """
            INSERT INTO chunks(
                chunk_uid,
                section_id,
                raw_text,
                clean_text,
                char_len,
                token_len,
                source_sha256,
                source_fetched_at,
                source_commit_sha,
                order_index
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk.chunk_uid,
                chunk.section_id,
                chunk.raw_text,
                chunk.clean_text,
                chunk.char_len,
                chunk.token_len,
                chunk.source_sha256,
                chunk.source_fetched_at,
                chunk.source_commit_sha,
                chunk.order_index,
            ),
        )
        connection.execute(
            """
            INSERT INTO chunks_fts(
                chunk_uid,
                section_id,
                section_heading,
                chunk_text
            ) VALUES(?, ?, ?, ?)
            """,
            (
                chunk.chunk_uid,
                chunk.section_id,
                section_heading_by_id.get(chunk.section_id, ""),
                chunk.clean_text,
            ),
        )

    for chunk_span in chunk_spans:
        connection.execute(
            """
            INSERT INTO chunk_spans(
                chunk_uid,
                source_anchor,
                start_offset,
                end_offset,
                span_order
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                chunk_span.chunk_uid,
                chunk_span.source_anchor,
                chunk_span.start_offset,
                chunk_span.end_offset,
                chunk_span.span_order,
            ),
        )

    for mechanism in mechanisms:
        connection.execute(
            """
            INSERT INTO mechanisms(
                mechanism_id,
                canonical_symbol,
                mechanism_family,
                enforcement_kind,
                stability
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                mechanism["mechanism_id"],
                mechanism["canonical_symbol"],
                mechanism["mechanism_family"],
                mechanism["enforcement_kind"],
                mechanism["stability"],
            ),
        )

    for evidence in mechanism_evidence:
        connection.execute(
            """
            INSERT INTO mechanism_evidence(
                evidence_id,
                mechanism_id,
                section_id,
                statement_id,
                source_anchor,
                evidence_kind,
                text_excerpt,
                confidence,
                source_fetched_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence["evidence_id"],
                evidence["mechanism_id"],
                evidence["section_id"],
                evidence["statement_id"],
                evidence["source_anchor"],
                evidence["evidence_kind"],
                evidence["text_excerpt"],
                evidence["confidence"],
                evidence["source_fetched_at"],
            ),
        )

    for row in table_rows:
        connection.execute(
            """
            INSERT INTO table1_rows(row_node_id, row_idx, row_marker, table_ref, requirement_text)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                row["row_node_id"],
                int(row["row_idx"]),
                row["row_marker"],
                "ISO26262-6-2018 Table 1",
                row["requirement_text"],
            ),
        )
        for footnote_order, footnote_text in enumerate(row.get("row_footnotes", []), start=1):
            connection.execute(
                """
                INSERT INTO table1_row_footnotes(row_node_id, footnote_order, footnote_text)
                VALUES(?, ?, ?)
                """,
                (
                    row["row_node_id"],
                    int(footnote_order),
                    str(footnote_text),
                ),
            )
        for term_order, term in enumerate(row.get("row_profile_terms", []), start=1):
            connection.execute(
                """
                INSERT INTO table1_row_profile_terms(row_node_id, term_order, term, term_source)
                VALUES(?, ?, ?, ?)
                """,
                (
                    row["row_node_id"],
                    int(term_order),
                    str(term),
                    "curated",
                ),
            )

    for verdict in row_verdicts:
        connection.execute(
            """
            INSERT INTO row_verdicts(
                row_node_id,
                verdict,
                rationale,
                rationale_anchor,
                rationale_timestamp
            )
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                verdict["row_node_id"],
                verdict["verdict"],
                verdict["rationale"],
                verdict["rationale_anchor"],
                verdict["rationale_timestamp"],
            ),
        )

    for row_mechanism in row_mechanisms:
        connection.execute(
            """
            INSERT INTO row_mechanisms(
                row_node_id,
                mechanism_id,
                relevance_score,
                evidence_anchor,
                evidence_section_id,
                evidence_statement_id,
                source_fetched_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_mechanism["row_node_id"],
                row_mechanism["mechanism_id"],
                row_mechanism["relevance_score"],
                row_mechanism["evidence_anchor"],
                row_mechanism["evidence_section_id"],
                row_mechanism["evidence_statement_id"],
                row_mechanism["source_fetched_at"],
            ),
        )

    for model in semantic_models:
        connection.execute(
            """
            INSERT INTO semantic_models(
                model_id,
                model_role,
                model_name,
                model_revision,
                embedding_dim,
                distance_metric,
                license,
                provider,
                retrieval_mode,
                created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model["model_id"],
                model["model_role"],
                model["model_name"],
                model["model_revision"],
                int(model["embedding_dim"]),
                model["distance_metric"],
                model["license"],
                model["provider"],
                model["retrieval_mode"],
                model["created_at"],
            ),
        )

    for corpus_row in semantic_corpus:
        connection.execute(
            """
            INSERT INTO semantic_corpus(
                corpus_id,
                source_kind,
                source_id,
                row_node_id,
                mechanism_id,
                source_anchor,
                text,
                text_sha256,
                source_fetched_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corpus_row["corpus_id"],
                corpus_row["source_kind"],
                corpus_row["source_id"],
                corpus_row["row_node_id"] or None,
                corpus_row["mechanism_id"] or None,
                corpus_row["source_anchor"],
                corpus_row["text"],
                corpus_row["text_sha256"],
                corpus_row["source_fetched_at"],
            ),
        )

    for score_row in row_mechanism_scores:
        connection.execute(
            """
            INSERT INTO row_mechanism_scores(
                row_node_id,
                mechanism_id,
                lexical_score,
                semantic_score,
                reranker_score,
                hybrid_score,
                score_version,
                top_statement_id,
                scored_at,
                source_fetched_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                score_row["row_node_id"],
                score_row["mechanism_id"],
                float(score_row["lexical_score"]),
                float(score_row["semantic_score"]),
                float(score_row["reranker_score"]),
                float(score_row["hybrid_score"]),
                score_row["score_version"],
                score_row["top_statement_id"] or None,
                score_row["scored_at"],
                score_row["source_fetched_at"],
            ),
        )
