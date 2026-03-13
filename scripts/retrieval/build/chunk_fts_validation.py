from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def chunk_mapping_applicable(connection: sqlite3.Connection) -> bool:
    names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    }
    return {"chunks", "chunks_fts"}.issubset(names)


def ensure_chunk_fts_mapping_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS chunk_fts_rowids(
            chunk_uid TEXT PRIMARY KEY,
            fts_rowid INTEGER NOT NULL UNIQUE
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chunk_fts_rowids_fts_rowid
        ON chunk_fts_rowids(fts_rowid)
        """
    )


def refresh_chunk_fts_rowids(connection: sqlite3.Connection) -> dict[str, int | bool]:
    ensure_chunk_fts_mapping_schema(connection)
    if not chunk_mapping_applicable(connection):
        return {"applicable": False, "refreshed_rows": 0}
    connection.execute("DELETE FROM chunk_fts_rowids")
    connection.execute(
        """
        INSERT INTO chunk_fts_rowids(chunk_uid, fts_rowid)
        SELECT chunk_uid, rowid
        FROM chunks_fts
        """
    )
    refreshed_rows = int(connection.execute("SELECT COUNT(*) FROM chunk_fts_rowids").fetchone()[0])
    return {"applicable": True, "refreshed_rows": refreshed_rows}


def _count_query(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(connection.execute(sql, params).fetchone()[0])


def validate_chunk_fts_mapping(
    connection: sqlite3.Connection,
    *,
    scoped_chunk_ids: set[str] | None = None,
) -> dict[str, Any]:
    applicable = chunk_mapping_applicable(connection)
    if not applicable:
        return {
            "applicable": False,
            "scope": "not_chunk_first",
            "passed": True,
        }

    ensure_chunk_fts_mapping_schema(connection)
    table_exists = bool(
        _count_query(
            connection,
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='chunk_fts_rowids'",
        )
    )
    if not table_exists:
        return {
            "applicable": True,
            "scope": "full" if scoped_chunk_ids is None else "scoped",
            "passed": False,
            "chunk_count": 0,
            "chunks_fts_count": 0,
            "chunk_fts_rowids_count": 0,
            "missing_mapping_count": 0,
            "orphan_mapping_count": 0,
            "fts_without_mapping_count": 0,
            "source_mismatch_count": 0,
            "duplicate_chunk_uid_count": 0,
            "duplicate_fts_rowid_count": 0,
            "validator_status": "missing_mapping_table",
        }

    scope = "full" if scoped_chunk_ids is None else "scoped"
    if scoped_chunk_ids is None:
        chunk_count = _count_query(connection, "SELECT COUNT(*) FROM chunks")
        chunks_fts_count = _count_query(connection, "SELECT COUNT(*) FROM chunks_fts")
        mapping_count = _count_query(connection, "SELECT COUNT(*) FROM chunk_fts_rowids")
        missing_mapping_count = _count_query(
            connection,
            """
            SELECT COUNT(*)
            FROM chunks AS c
            LEFT JOIN chunk_fts_rowids AS m ON m.chunk_uid = c.chunk_uid
            WHERE m.chunk_uid IS NULL
            """,
        )
        orphan_mapping_count = _count_query(
            connection,
            """
            SELECT COUNT(*)
            FROM chunk_fts_rowids AS m
            LEFT JOIN chunks AS c ON c.chunk_uid = m.chunk_uid
            WHERE c.chunk_uid IS NULL
            """,
        )
        fts_without_mapping_count = _count_query(
            connection,
            """
            SELECT COUNT(*)
            FROM chunks_fts AS f
            LEFT JOIN chunk_fts_rowids AS m ON m.fts_rowid = f.rowid
            WHERE m.fts_rowid IS NULL
            """,
        )
        source_mismatch_count = _count_query(
            connection,
            """
            SELECT COUNT(*)
            FROM chunk_fts_rowids AS m
            JOIN chunks_fts AS f ON f.rowid = m.fts_rowid
            WHERE f.chunk_uid != m.chunk_uid
            """,
        )
    else:
        allowed = sorted({str(value) for value in scoped_chunk_ids if str(value).strip()})
        if not allowed:
            return {
                "applicable": True,
                "scope": "scoped",
                "passed": True,
                "chunk_count": 0,
                "chunks_fts_count": 0,
                "chunk_fts_rowids_count": 0,
                "missing_mapping_count": 0,
                "orphan_mapping_count": 0,
                "fts_without_mapping_count": 0,
                "source_mismatch_count": 0,
                "duplicate_chunk_uid_count": 0,
                "duplicate_fts_rowid_count": 0,
                "validator_status": "scoped_empty",
            }
        placeholders = ", ".join("?" for _ in allowed)
        params = tuple(allowed)
        chunk_count = _count_query(
            connection, f"SELECT COUNT(*) FROM chunks WHERE chunk_uid IN ({placeholders})", params
        )
        chunks_fts_count = _count_query(
            connection,
            f"SELECT COUNT(*) FROM chunks_fts WHERE chunk_uid IN ({placeholders})",
            params,
        )
        mapping_count = _count_query(
            connection,
            f"SELECT COUNT(*) FROM chunk_fts_rowids WHERE chunk_uid IN ({placeholders})",
            params,
        )
        missing_mapping_count = _count_query(
            connection,
            f"""
            SELECT COUNT(*)
            FROM chunks AS c
            LEFT JOIN chunk_fts_rowids AS m ON m.chunk_uid = c.chunk_uid
            WHERE c.chunk_uid IN ({placeholders})
              AND m.chunk_uid IS NULL
            """,
            params,
        )
        orphan_mapping_count = _count_query(
            connection,
            f"SELECT COUNT(*) FROM chunk_fts_rowids WHERE chunk_uid IN ({placeholders}) AND chunk_uid NOT IN (SELECT chunk_uid FROM chunks)",
            params,
        )
        fts_without_mapping_count = _count_query(
            connection,
            f"""
            SELECT COUNT(*)
            FROM chunks_fts AS f
            LEFT JOIN chunk_fts_rowids AS m ON m.fts_rowid = f.rowid
            WHERE f.chunk_uid IN ({placeholders})
              AND m.fts_rowid IS NULL
            """,
            params,
        )
        source_mismatch_count = _count_query(
            connection,
            f"""
            SELECT COUNT(*)
            FROM chunk_fts_rowids AS m
            JOIN chunks_fts AS f ON f.rowid = m.fts_rowid
            WHERE m.chunk_uid IN ({placeholders})
              AND f.chunk_uid != m.chunk_uid
            """,
            params,
        )

    duplicate_chunk_uid_count = _count_query(
        connection,
        """
        SELECT COUNT(*)
        FROM (
            SELECT chunk_uid
            FROM chunk_fts_rowids
            GROUP BY chunk_uid
            HAVING COUNT(*) > 1
        )
        """,
    )
    duplicate_fts_rowid_count = _count_query(
        connection,
        """
        SELECT COUNT(*)
        FROM (
            SELECT fts_rowid
            FROM chunk_fts_rowids
            GROUP BY fts_rowid
            HAVING COUNT(*) > 1
        )
        """,
    )
    passed = (
        chunk_count == chunks_fts_count == mapping_count
        and missing_mapping_count == 0
        and orphan_mapping_count == 0
        and fts_without_mapping_count == 0
        and source_mismatch_count == 0
        and duplicate_chunk_uid_count == 0
        and duplicate_fts_rowid_count == 0
    )
    return {
        "applicable": True,
        "scope": scope,
        "passed": passed,
        "chunk_count": chunk_count,
        "chunks_fts_count": chunks_fts_count,
        "chunk_fts_rowids_count": mapping_count,
        "missing_mapping_count": missing_mapping_count,
        "orphan_mapping_count": orphan_mapping_count,
        "fts_without_mapping_count": fts_without_mapping_count,
        "source_mismatch_count": source_mismatch_count,
        "duplicate_chunk_uid_count": duplicate_chunk_uid_count,
        "duplicate_fts_rowid_count": duplicate_fts_rowid_count,
        "validator_status": "pass" if passed else "failed",
    }


def validate_chunk_fts_mapping_db(
    db_path: Path,
    *,
    scoped_chunk_ids: set[str] | None = None,
) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    try:
        return validate_chunk_fts_mapping(connection, scoped_chunk_ids=scoped_chunk_ids)
    finally:
        connection.close()


def enforce_chunk_fts_mapping(
    connection: sqlite3.Connection,
    *,
    scoped_chunk_ids: set[str] | None = None,
    context: str,
) -> dict[str, Any]:
    diagnostics = validate_chunk_fts_mapping(connection, scoped_chunk_ids=scoped_chunk_ids)
    if diagnostics.get("applicable") and not diagnostics.get("passed", False):
        raise RuntimeError(f"chunk_fts_rowids validation failed during {context}: {diagnostics}")
    return diagnostics
