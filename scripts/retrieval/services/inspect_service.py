from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from pathlib import Path

from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.services.capability import emit_unsupported


def _table_count(connection: sqlite3.Connection, table_name: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    if row is None or int(row[0]) == 0:
        return 0
    count_row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return 0 if count_row is None else int(count_row[0])


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    return row is not None


def run(args: Namespace, *, root: Path) -> int:
    defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))
    if not defaults.supports_inspect:
        return emit_unsupported(
            corpus=defaults.corpus,
            operation="inspect",
            reason="corpus configuration disables inspect",
        )

    db_path = defaults.db_path
    if not db_path.exists():
        print(
            json.dumps(
                {
                    "status": "inspect_ok",
                    "corpus": defaults.corpus,
                    "db_path": str(db_path),
                    "warning": "database_not_found",
                    "guidelines": 0,
                    "exemplars": 0,
                    "blocks": 0,
                    "bibliography": 0,
                    "exports": 0,
                },
                sort_keys=True,
            )
        )
        return 0

    connection = sqlite3.connect(db_path)
    try:
        guidelines_preview: list[dict[str, str]] = []
        exemplars_preview: list[dict[str, str]] = []
        latest_exports: list[dict[str, str]] = []
        if _table_exists(connection, "guideline_records"):
            rows = connection.execute(
                """
                SELECT guideline_id, title, quality_label, export_topic
                FROM guideline_records
                ORDER BY guideline_id ASC
                LIMIT 25
                """
            ).fetchall()
            guidelines_preview = [
                {
                    "guideline_id": str(row[0]),
                    "title": str(row[1]),
                    "quality_label": str(row[2]),
                    "export_topic": str(row[3]),
                }
                for row in rows
            ]
        if _table_exists(connection, "guideline_exemplars"):
            rows = connection.execute(
                """
                SELECT guideline_id, added_at, rationale
                FROM guideline_exemplars
                ORDER BY guideline_id ASC
                LIMIT 25
                """
            ).fetchall()
            exemplars_preview = [
                {
                    "guideline_id": str(row[0]),
                    "added_at": str(row[1]),
                    "rationale": str(row[2]),
                }
                for row in rows
            ]
        if _table_exists(connection, "guideline_export_runs"):
            rows = connection.execute(
                """
                SELECT run_id, source_revision, output_root, file_count, output_digest, created_at
                FROM guideline_export_runs
                ORDER BY created_at DESC
                LIMIT 10
                """
            ).fetchall()
            latest_exports = [
                {
                    "run_id": str(row[0]),
                    "source_revision": str(row[1]),
                    "output_root": str(row[2]),
                    "file_count": str(row[3]),
                    "output_digest": str(row[4]),
                    "created_at": str(row[5]),
                }
                for row in rows
            ]
        payload = {
            "status": "inspect_ok",
            "corpus": defaults.corpus,
            "db_path": str(db_path),
            "guidelines": _table_count(connection, "guideline_records"),
            "exemplars": _table_count(connection, "guideline_exemplars"),
            "blocks": _table_count(connection, "guideline_blocks"),
            "bibliography": _table_count(connection, "guideline_bibliography"),
            "exports": _table_count(connection, "guideline_export_runs"),
            "guidelines_preview": guidelines_preview,
            "exemplars_preview": exemplars_preview,
            "latest_exports": latest_exports,
        }
    finally:
        connection.close()

    print(json.dumps(payload, sort_keys=True))
    return 0
