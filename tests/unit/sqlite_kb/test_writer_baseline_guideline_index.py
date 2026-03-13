from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.baseline_guideline_index import load_baseline_guideline_index  # noqa: E402,I001


def test_load_baseline_guideline_index_reads_normalized_guidelines(tmp_path: Path) -> None:
    db_path = tmp_path / "guidelines_repo.sqlite"
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE guideline_records(
                guideline_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_file_path TEXT NOT NULL,
                quality_label TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                export_topic TEXT NOT NULL,
                source_revision TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                ingested_at TEXT NOT NULL
            );
            CREATE TABLE guideline_blocks(
                block_id TEXT PRIMARY KEY,
                guideline_id TEXT NOT NULL,
                block_type TEXT NOT NULL,
                order_index INTEGER NOT NULL,
                content TEXT NOT NULL
            );
            CREATE TABLE guideline_fls_source_mappings(
                guideline_id TEXT PRIMARY KEY,
                source_file_path TEXT NOT NULL,
                raw_fls_id TEXT NOT NULL,
                raw_fls_present INTEGER NOT NULL,
                source_revision TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                last_ingested_at TEXT NOT NULL
            );
            CREATE TABLE guideline_fls_resolution_overrides(
                guideline_id TEXT PRIMARY KEY,
                effective_fls_id TEXT NOT NULL,
                resolution_kind TEXT NOT NULL,
                resolution_status TEXT NOT NULL,
                audit_run_id TEXT NOT NULL,
                evidence_source_id TEXT NOT NULL,
                rationale_text TEXT NOT NULL,
                approved_by TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO guideline_records VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "gui_existing",
                "Require explicit ownership transfer on API boundaries",
                "src/coding-guidelines/ownership-and-destruction/gui_existing.rst",
                "published",
                '{"tags": ["ownership", "api-boundary"], "fls": "fls_123"}',
                "ownership-and-destruction",
                "rev",
                "hash",
                "now",
            ),
        )
        connection.execute(
            "INSERT INTO guideline_blocks VALUES(?, ?, ?, ?, ?)",
            (
                "gui_existing:body:1",
                "gui_existing",
                "body",
                1,
                "Ownership transfer shall be explicit at API boundaries.",
            ),
        )
        connection.execute(
            "INSERT INTO guideline_fls_source_mappings VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                "gui_existing",
                "src/coding-guidelines/ownership-and-destruction/gui_existing.rst",
                "fls_123",
                1,
                "rev",
                "hash",
                "now",
            ),
        )
        connection.execute(
            "INSERT INTO guideline_fls_resolution_overrides VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "gui_existing",
                "fls_effective",
                "remap",
                "approved",
                "audit",
                "source",
                "better paragraph",
                "tester",
                "now",
                "now",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    rows = load_baseline_guideline_index(root=ROOT, db_path=db_path)

    assert rows[0]["guideline_id"] == "gui_existing"
    assert rows[0]["chapter"] == "ownership-and-destruction"
    assert "ownership" in rows[0]["construct_keywords"]
    assert rows[0]["fls_id"] == "fls_effective"
