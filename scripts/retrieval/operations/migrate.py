#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from retrieval.build.chunk_fts_validation import (
    enforce_chunk_fts_mapping,
    refresh_chunk_fts_rowids,
)

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def _list_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _count_rows(connection: sqlite3.Connection, table_name: str) -> int:
    if not _table_exists(connection, table_name):
        return 0
    return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _ensure_column(
    connection: sqlite3.Connection, table_name: str, ddl_suffix: str, column_name: str
) -> bool:
    if not _table_exists(connection, table_name):
        return False
    if column_name in _list_columns(connection, table_name):
        return False
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl_suffix}")
    return True


def migrate_schema(db_path: Path) -> dict[str, object]:
    if not db_path.exists():
        raise RuntimeError(f"Database not found for migration: {db_path}")

    connection = sqlite3.connect(db_path)
    try:
        added_columns: list[str] = []
        refreshed_fts = False
        refreshed_chunk_fts = False
        migrated_docs = 0
        migrated_chunks = 0
        migrated_row_profile_terms = 0
        chunk_mapping_refreshed = False
        chunk_mapping_diagnostics: dict[str, object] = {
            "applicable": False,
            "scope": "not_chunk_first",
            "passed": True,
        }

        if _ensure_column(
            connection, "snapshots", "commit_sha TEXT NOT NULL DEFAULT 'unknown'", "commit_sha"
        ):
            added_columns.append("snapshots.commit_sha")
        if _ensure_column(
            connection, "snapshots", "source_url TEXT NOT NULL DEFAULT ''", "source_url"
        ):
            added_columns.append("snapshots.source_url")

        if _ensure_column(
            connection,
            "table1_rows",
            "requirement_text TEXT NOT NULL DEFAULT ''",
            "requirement_text",
        ):
            added_columns.append("table1_rows.requirement_text")

        if _ensure_column(
            connection,
            "row_verdicts",
            "rationale_anchor TEXT NOT NULL DEFAULT ''",
            "rationale_anchor",
        ):
            added_columns.append("row_verdicts.rationale_anchor")
        if _ensure_column(
            connection,
            "row_verdicts",
            f"rationale_timestamp TEXT NOT NULL DEFAULT '{utc_now()}'",
            "rationale_timestamp",
        ):
            added_columns.append("row_verdicts.rationale_timestamp")

        if _ensure_column(
            connection,
            "row_mechanisms",
            "evidence_anchor TEXT NOT NULL DEFAULT ''",
            "evidence_anchor",
        ):
            added_columns.append("row_mechanisms.evidence_anchor")
        if _ensure_column(
            connection,
            "row_mechanisms",
            "evidence_section_id TEXT NOT NULL DEFAULT ''",
            "evidence_section_id",
        ):
            added_columns.append("row_mechanisms.evidence_section_id")
        if _ensure_column(
            connection,
            "row_mechanisms",
            "evidence_statement_id TEXT NOT NULL DEFAULT ''",
            "evidence_statement_id",
        ):
            added_columns.append("row_mechanisms.evidence_statement_id")
        if _ensure_column(
            connection,
            "row_mechanisms",
            f"source_fetched_at TEXT NOT NULL DEFAULT '{utc_now()}'",
            "source_fetched_at",
        ):
            added_columns.append("row_mechanisms.source_fetched_at")

        if _table_exists(connection, "statements"):
            connection.execute("DROP TABLE IF EXISTS statements_fts")
            connection.execute(
                """
                CREATE VIRTUAL TABLE statements_fts
                USING fts5(
                    statement_id UNINDEXED,
                    section_id UNINDEXED,
                    section_heading,
                    statement_text,
                    tokenize='unicode61 remove_diacritics 2'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO statements_fts(
                    statement_id,
                    section_id,
                    section_heading,
                    statement_text
                )
                SELECT
                    s.statement_id,
                    s.section_id,
                    COALESCE(sec.heading, ''),
                    s.text
                FROM statements AS s
                LEFT JOIN sections AS sec ON sec.section_id = s.section_id
                ORDER BY s.statement_id ASC
                """
            )
            refreshed_fts = True

        if _table_exists(connection, "docs") and _table_exists(connection, "source_documents"):
            if _count_rows(connection, "docs") == 0:
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
                    )
                    SELECT
                        document_id,
                        rel_path,
                        title,
                        source_commit_sha,
                        source_fetched_at,
                        source_sha256,
                        chapter_id,
                        order_index
                    FROM source_documents
                    ORDER BY order_index ASC
                    """
                )
                migrated_docs = _count_rows(connection, "docs")

        if _table_exists(connection, "chunks") and _table_exists(connection, "statements"):
            if _count_rows(connection, "chunks") == 0:
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
                    )
                    SELECT
                        statement_id,
                        section_id,
                        text,
                        TRIM(text),
                        LENGTH(text),
                        CASE
                            WHEN TRIM(text) = '' THEN 0
                            ELSE LENGTH(TRIM(text)) - LENGTH(REPLACE(TRIM(text), ' ', '')) + 1
                        END,
                        source_sha256,
                        source_fetched_at,
                        source_commit_sha,
                        sentence_index
                    FROM statements
                    ORDER BY statement_id ASC
                    """
                )
                connection.execute(
                    """
                    INSERT INTO chunk_spans(
                        chunk_uid,
                        source_anchor,
                        start_offset,
                        end_offset,
                        span_order
                    )
                    SELECT
                        s.statement_id,
                        (
                          'https://doc.rust-lang.org/reference/' ||
                          CASE
                            WHEN sd.rel_path LIKE '%.md'
                              THEN substr(sd.rel_path, 1, length(sd.rel_path) - 3) || '.html'
                            ELSE sd.rel_path
                          END ||
                          '#' || sec.anchor
                        ) AS source_anchor,
                        0,
                        LENGTH(s.text),
                        1
                    FROM statements AS s
                    JOIN sections AS sec ON sec.section_id = s.section_id
                    JOIN source_documents AS sd ON sd.document_id = sec.document_id
                    ORDER BY s.statement_id ASC
                    """
                )
                migrated_chunks = _count_rows(connection, "chunks")

        if _table_exists(connection, "chunks"):
            connection.execute("DROP TABLE IF EXISTS chunks_fts")
            connection.execute(
                """
                CREATE VIRTUAL TABLE chunks_fts
                USING fts5(
                    chunk_uid UNINDEXED,
                    section_id UNINDEXED,
                    section_heading,
                    chunk_text,
                    tokenize='unicode61 remove_diacritics 2'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO chunks_fts(
                    chunk_uid,
                    section_id,
                    section_heading,
                    chunk_text
                )
                SELECT
                    c.chunk_uid,
                    c.section_id,
                    COALESCE(sec.heading, ''),
                    c.clean_text
                FROM chunks AS c
                LEFT JOIN sections AS sec ON sec.section_id = c.section_id
                ORDER BY c.chunk_uid ASC
                """
            )
            refreshed_chunk_fts = True

        if _table_exists(connection, "chunks") and _table_exists(connection, "chunks_fts"):
            refresh_chunk_fts_rowids(connection)
            chunk_mapping_refreshed = True
            chunk_mapping_diagnostics = enforce_chunk_fts_mapping(
                connection,
                context="rust_reference schema migrate",
            )

        if _table_exists(connection, "table1_row_profile_terms") and _table_exists(
            connection, "table1_rows"
        ):
            existing_profile_terms = _count_rows(connection, "table1_row_profile_terms")
            if existing_profile_terms == 0:
                rows = connection.execute(
                    "SELECT row_node_id, requirement_text FROM table1_rows ORDER BY row_idx ASC"
                ).fetchall()
                payload: list[tuple[str, int, str, str]] = []
                for row in rows:
                    row_node_id = str(row[0])
                    requirement_text = str(row[1] or "").lower()
                    terms = []
                    seen: set[str] = set()
                    for token in TOKEN_RE.findall(requirement_text):
                        if token in seen:
                            continue
                        seen.add(token)
                        terms.append(token)
                        if len(terms) >= 8:
                            break
                    for term_order, term in enumerate(terms, start=1):
                        payload.append((row_node_id, term_order, term, "migrated"))

                if payload:
                    connection.executemany(
                        """
                        INSERT INTO table1_row_profile_terms(
                            row_node_id,
                            term_order,
                            term,
                            term_source
                        ) VALUES(?, ?, ?, ?)
                        """,
                        payload,
                    )
                    migrated_row_profile_terms = len(payload)

        connection.execute("PRAGMA user_version = 7")
        connection.commit()
    finally:
        connection.close()

    return {
        "db_path": str(db_path),
        "added_columns": added_columns,
        "refreshed_statements_fts": refreshed_fts,
        "refreshed_chunks_fts": refreshed_chunk_fts,
        "refreshed_chunk_fts_rowids": chunk_mapping_refreshed,
        "migrated_docs": migrated_docs,
        "migrated_chunks": migrated_chunks,
        "migrated_row_profile_terms": migrated_row_profile_terms,
        "chunk_fts_mapping": chunk_mapping_diagnostics,
        "target_user_version": 7,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate rust_reference sqlite schema to current version"
    )
    parser.add_argument(
        "--db-path",
        default=".cache/sqlite_kb/current/rust_reference.sqlite",
        help="Path to rust_reference sqlite file",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[3]
    db_path = (root / args.db_path).resolve()

    try:
        summary = migrate_schema(db_path)
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"[migrate-rust-reference][error] {exc}")
        return EXIT_RUNTIME_FAIL

    print(json.dumps(summary, indent=2, sort_keys=True))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
