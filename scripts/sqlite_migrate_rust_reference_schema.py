#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from sqlite_build_rust_reference import initialize_schema, utc_now

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
        initialize_schema(connection)
        added_columns: list[str] = []
        refreshed_fts = False

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

        connection.execute("PRAGMA user_version = 5")
        connection.commit()
    finally:
        connection.close()

    return {
        "db_path": str(db_path),
        "added_columns": added_columns,
        "refreshed_statements_fts": refreshed_fts,
        "target_user_version": 5,
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
    root = Path(__file__).resolve().parents[1]
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
