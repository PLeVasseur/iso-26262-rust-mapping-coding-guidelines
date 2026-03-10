from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.build.reports import (  # noqa: E402
    validate_guidelines_repo_db,
    write_current_guidelines_repo_validation_report,
)


class GuidelinesRepoValidationReportTests(unittest.TestCase):
    def _build_db(self, db_path: Path) -> None:
        connection = sqlite3.connect(db_path)
        try:
            connection.executescript(
                """
                PRAGMA user_version=7;
                CREATE TABLE guideline_records (
                    guideline_id TEXT PRIMARY KEY,
                    title TEXT,
                    source_file_path TEXT,
                    quality_label TEXT,
                    metadata_json TEXT,
                    export_topic TEXT,
                    source_revision TEXT,
                    source_hash TEXT,
                    ingested_at TEXT
                );
                CREATE TABLE guideline_blocks (
                    block_id TEXT PRIMARY KEY,
                    guideline_id TEXT,
                    block_type TEXT,
                    order_index INTEGER,
                    content TEXT
                );
                CREATE TABLE guideline_citations (
                    citation_id TEXT PRIMARY KEY,
                    guideline_id TEXT,
                    block_id TEXT,
                    ref_target TEXT,
                    order_index INTEGER
                );
                CREATE TABLE guideline_bibliography (
                    bib_key TEXT PRIMARY KEY,
                    content TEXT,
                    source_file_path TEXT
                );
                CREATE TABLE guideline_bib_links (
                    guideline_id TEXT,
                    bib_key TEXT
                );
                CREATE TABLE guideline_exemplars (
                    guideline_id TEXT PRIMARY KEY,
                    added_at TEXT,
                    rationale TEXT
                );
                CREATE TABLE snapshots (
                    snapshot_id TEXT,
                    commit_sha TEXT,
                    source_url TEXT,
                    fetched_at TEXT,
                    sha256 TEXT
                );
                CREATE TABLE schema_version (
                    schema_id TEXT PRIMARY KEY,
                    latest_migration_id TEXT,
                    schema_version INTEGER,
                    updated_at TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO guideline_records VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "gui_001",
                    "Example",
                    "src/coding-guidelines/example.rst",
                    "mixed",
                    "{}",
                    "Example",
                    "rev1",
                    "hash1",
                    "2026-03-10T00:00:00+00:00",
                ),
            )
            connection.execute(
                "INSERT INTO guideline_blocks VALUES(?, ?, ?, ?, ?)",
                ("gui_001:body:1", "gui_001", "body", 1, "content"),
            )
            connection.execute(
                "INSERT INTO guideline_citations VALUES(?, ?, ?, ?, ?)",
                ("cite1", "gui_001", "gui_001:body:1", "ref", 1),
            )
            connection.execute(
                "INSERT INTO guideline_bibliography VALUES(?, ?, ?)",
                ("bib1", "entry", "src/coding-guidelines/example.rst"),
            )
            connection.execute(
                "INSERT INTO guideline_bib_links VALUES(?, ?)",
                ("gui_001", "bib1"),
            )
            connection.execute(
                "INSERT INTO snapshots VALUES(?, ?, ?, ?, ?)",
                (
                    "guidelines-repo-1",
                    "abcdef123",
                    "https://example.invalid/repo",
                    "2026-03-10T00:00:00+00:00",
                    "sha",
                ),
            )
            connection.execute(
                "INSERT INTO schema_version VALUES(?, ?, ?, ?)",
                ("sqlite_kb", "20260309_005_chunk_fts_rowids", 7, "2026-03-10T00:00:00+00:00"),
            )
            connection.commit()
        finally:
            connection.close()

    def test_validate_guidelines_repo_db_and_write_current_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "guidelines_repo.sqlite"
            self._build_db(db_path)

            report = validate_guidelines_repo_db(db_path)
            self.assertTrue(report["passed"], report)
            self.assertEqual(report["corpus"], "guidelines_repo")
            self.assertEqual(report["latest_snapshot_id"], "guidelines-repo-1")
            self.assertEqual(report["latest_commit_sha"], "abcdef123")
            self.assertEqual(report["table_counts"]["guideline_records"], 1)
            self.assertEqual(report["table_counts"]["guideline_blocks"], 1)

            current_path = write_current_guidelines_repo_validation_report(
                report_root=temp_root / "reports" / "guidelines_repo",
                payload=report,
            )
            self.assertTrue(current_path.is_file())
            payload = json.loads(current_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["corpus"], "guidelines_repo")
            self.assertTrue(payload["passed"])


if __name__ == "__main__":
    unittest.main()
