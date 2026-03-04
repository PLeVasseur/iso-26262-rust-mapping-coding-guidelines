from __future__ import annotations

import sqlite3


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS snapshots (
            snapshot_id TEXT PRIMARY KEY,
            commit_sha TEXT NOT NULL,
            source_url TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            sha256 TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS kb_metadata (
            kb_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            source_revision TEXT NOT NULL,
            extractor_version TEXT NOT NULL,
            built_at TEXT NOT NULL,
            notes TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chapters (
            chapter_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            order_index INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_documents (
            document_id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            rel_path TEXT NOT NULL,
            title TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            source_commit_sha TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            UNIQUE(snapshot_id, rel_path),
            FOREIGN KEY(snapshot_id) REFERENCES snapshots(snapshot_id),
            FOREIGN KEY(chapter_id) REFERENCES chapters(chapter_id)
        );

        CREATE TABLE IF NOT EXISTS docs (
            doc_uid TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            title TEXT NOT NULL,
            revision TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            FOREIGN KEY(chapter_id) REFERENCES chapters(chapter_id)
        );

        CREATE TABLE IF NOT EXISTS sections (
            section_id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            anchor TEXT NOT NULL,
            heading TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            level INTEGER NOT NULL,
            text TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            source_commit_sha TEXT NOT NULL,
            FOREIGN KEY(snapshot_id) REFERENCES snapshots(snapshot_id),
            FOREIGN KEY(document_id) REFERENCES source_documents(document_id),
            FOREIGN KEY(chapter_id) REFERENCES chapters(chapter_id)
        );

        CREATE TABLE IF NOT EXISTS statements (
            statement_id TEXT PRIMARY KEY,
            section_id TEXT NOT NULL,
            statement_type TEXT NOT NULL
                CHECK(statement_type IN ('definition', 'constraint', 'behavior')),
            text TEXT NOT NULL,
            confidence REAL NOT NULL,
            sentence_index INTEGER NOT NULL,
            source_sha256 TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            source_commit_sha TEXT NOT NULL,
            FOREIGN KEY(section_id) REFERENCES sections(section_id)
        );

        CREATE TABLE IF NOT EXISTS chunks (
            chunk_uid TEXT PRIMARY KEY,
            section_id TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            clean_text TEXT NOT NULL,
            char_len INTEGER NOT NULL,
            token_len INTEGER NOT NULL,
            source_sha256 TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            source_commit_sha TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            FOREIGN KEY(section_id) REFERENCES sections(section_id)
        );

        CREATE TABLE IF NOT EXISTS chunk_spans (
            chunk_uid TEXT NOT NULL,
            source_anchor TEXT NOT NULL,
            start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL,
            span_order INTEGER NOT NULL,
            PRIMARY KEY (chunk_uid, span_order),
            FOREIGN KEY(chunk_uid) REFERENCES chunks(chunk_uid)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
        USING fts5(
            chunk_uid UNINDEXED,
            section_id UNINDEXED,
            section_heading,
            chunk_text,
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS statements_fts
        USING fts5(
            statement_id UNINDEXED,
            section_id UNINDEXED,
            section_heading,
            statement_text,
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE TABLE IF NOT EXISTS mechanisms (
            mechanism_id TEXT PRIMARY KEY,
            canonical_symbol TEXT NOT NULL,
            mechanism_family TEXT NOT NULL,
            enforcement_kind TEXT NOT NULL,
            stability TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS mechanism_evidence (
            evidence_id TEXT PRIMARY KEY,
            mechanism_id TEXT NOT NULL,
            section_id TEXT NOT NULL,
            statement_id TEXT,
            source_anchor TEXT NOT NULL,
            evidence_kind TEXT NOT NULL,
            text_excerpt TEXT NOT NULL,
            confidence REAL NOT NULL,
            source_fetched_at TEXT NOT NULL,
            FOREIGN KEY(mechanism_id) REFERENCES mechanisms(mechanism_id),
            FOREIGN KEY(section_id) REFERENCES sections(section_id),
            FOREIGN KEY(statement_id) REFERENCES statements(statement_id)
        );

        CREATE TABLE IF NOT EXISTS table1_rows (
            row_node_id TEXT PRIMARY KEY,
            row_idx INTEGER NOT NULL,
            row_marker TEXT NOT NULL,
            table_ref TEXT NOT NULL,
            requirement_text TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS table1_row_footnotes (
            row_node_id TEXT NOT NULL,
            footnote_order INTEGER NOT NULL,
            footnote_text TEXT NOT NULL,
            PRIMARY KEY(row_node_id, footnote_order),
            FOREIGN KEY(row_node_id) REFERENCES table1_rows(row_node_id)
        );

        CREATE TABLE IF NOT EXISTS table1_row_profile_terms (
            row_node_id TEXT NOT NULL,
            term_order INTEGER NOT NULL,
            term TEXT NOT NULL,
            term_source TEXT NOT NULL,
            PRIMARY KEY(row_node_id, term_order),
            FOREIGN KEY(row_node_id) REFERENCES table1_rows(row_node_id)
        );

        CREATE TABLE IF NOT EXISTS row_verdicts (
            row_node_id TEXT PRIMARY KEY,
            verdict TEXT NOT NULL CHECK(verdict IN ('applicable', 'not_applicable')),
            rationale TEXT NOT NULL,
            rationale_anchor TEXT NOT NULL,
            rationale_timestamp TEXT NOT NULL,
            FOREIGN KEY(row_node_id) REFERENCES table1_rows(row_node_id)
        );

        CREATE TABLE IF NOT EXISTS row_mechanisms (
            row_node_id TEXT NOT NULL,
            mechanism_id TEXT NOT NULL,
            relevance_score REAL NOT NULL,
            evidence_anchor TEXT NOT NULL,
            evidence_section_id TEXT NOT NULL,
            evidence_statement_id TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            PRIMARY KEY (row_node_id, mechanism_id),
            FOREIGN KEY(row_node_id) REFERENCES table1_rows(row_node_id),
            FOREIGN KEY(mechanism_id) REFERENCES mechanisms(mechanism_id)
        );

        CREATE TABLE IF NOT EXISTS semantic_models (
            model_id TEXT PRIMARY KEY,
            model_role TEXT NOT NULL CHECK(model_role IN ('embedder', 'reranker')),
            model_name TEXT NOT NULL,
            model_revision TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL,
            distance_metric TEXT NOT NULL,
            license TEXT NOT NULL,
            provider TEXT NOT NULL,
            retrieval_mode TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS semantic_corpus (
            corpus_id TEXT PRIMARY KEY,
            source_kind TEXT NOT NULL,
            source_id TEXT NOT NULL,
            row_node_id TEXT,
            mechanism_id TEXT,
            source_anchor TEXT NOT NULL,
            text TEXT NOT NULL,
            text_sha256 TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            FOREIGN KEY(row_node_id) REFERENCES table1_rows(row_node_id),
            FOREIGN KEY(mechanism_id) REFERENCES mechanisms(mechanism_id)
        );

        CREATE TABLE IF NOT EXISTS statement_embeddings (
            statement_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            text_sha256 TEXT NOT NULL,
            vector_json TEXT NOT NULL,
            vector_norm REAL NOT NULL,
            embedded_at TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            PRIMARY KEY(statement_id, model_id),
            FOREIGN KEY(statement_id) REFERENCES statements(statement_id)
        );

        CREATE TABLE IF NOT EXISTS chunk_embeddings (
            chunk_uid TEXT NOT NULL,
            model_id TEXT NOT NULL,
            embed_version TEXT NOT NULL,
            text_sha256 TEXT NOT NULL,
            vector_json TEXT NOT NULL,
            vector_norm REAL NOT NULL,
            embedded_at TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            PRIMARY KEY(chunk_uid, model_id, embed_version),
            FOREIGN KEY(chunk_uid) REFERENCES chunks(chunk_uid)
        );

        CREATE TABLE IF NOT EXISTS row_mechanism_scores (
            row_node_id TEXT NOT NULL,
            mechanism_id TEXT NOT NULL,
            lexical_score REAL NOT NULL,
            semantic_score REAL NOT NULL,
            reranker_score REAL NOT NULL,
            hybrid_score REAL NOT NULL,
            score_version TEXT NOT NULL,
            top_statement_id TEXT,
            scored_at TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            PRIMARY KEY (row_node_id, mechanism_id),
            FOREIGN KEY(row_node_id) REFERENCES table1_rows(row_node_id),
            FOREIGN KEY(mechanism_id) REFERENCES mechanisms(mechanism_id),
            FOREIGN KEY(top_statement_id) REFERENCES statements(statement_id)
        );

        CREATE INDEX IF NOT EXISTS idx_chapters_order ON chapters(order_index);
        CREATE INDEX IF NOT EXISTS idx_documents_chapter
            ON source_documents(chapter_id, order_index);
        CREATE INDEX IF NOT EXISTS idx_docs_chapter
            ON docs(chapter_id, order_index);
        CREATE INDEX IF NOT EXISTS idx_sections_document ON sections(document_id, order_index);
        CREATE INDEX IF NOT EXISTS idx_sections_chapter ON sections(chapter_id, order_index);
        CREATE INDEX IF NOT EXISTS idx_statements_section ON statements(section_id, sentence_index);
        CREATE INDEX IF NOT EXISTS idx_chunks_section ON chunks(section_id, order_index);
        CREATE INDEX IF NOT EXISTS idx_chunk_spans_anchor ON chunk_spans(source_anchor, chunk_uid);
        CREATE INDEX IF NOT EXISTS idx_mechanism_evidence_mech ON mechanism_evidence(mechanism_id);
        CREATE INDEX IF NOT EXISTS idx_table1_rows_marker ON table1_rows(row_marker);
        CREATE INDEX IF NOT EXISTS idx_table1_row_footnotes_row
            ON table1_row_footnotes(row_node_id, footnote_order);
        CREATE INDEX IF NOT EXISTS idx_table1_row_profile_terms_row
            ON table1_row_profile_terms(row_node_id, term_order);
        CREATE INDEX IF NOT EXISTS idx_row_mechanisms_row ON row_mechanisms(row_node_id);
        CREATE INDEX IF NOT EXISTS idx_semantic_corpus_source
            ON semantic_corpus(source_kind, source_id);
        CREATE INDEX IF NOT EXISTS idx_semantic_corpus_mechanism
            ON semantic_corpus(mechanism_id, source_kind);
        CREATE INDEX IF NOT EXISTS idx_statement_embeddings_model
            ON statement_embeddings(model_id, statement_id);
        CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_model
            ON chunk_embeddings(model_id, chunk_uid, embed_version);
        CREATE INDEX IF NOT EXISTS idx_row_mechanism_scores_row
            ON row_mechanism_scores(row_node_id, hybrid_score DESC);

        PRAGMA user_version = 6;
        """
    )
