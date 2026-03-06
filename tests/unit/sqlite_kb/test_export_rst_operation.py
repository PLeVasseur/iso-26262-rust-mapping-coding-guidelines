from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.operations.export_rst import export_guidelines  # noqa: E402


class ExportRstOperationTests(unittest.TestCase):
    def test_exports_to_chapter_sidecar_and_syncs_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "guidelines.sqlite"
            output_root = root / "src" / "coding-guidelines"

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
                    CREATE TABLE guideline_bibliography(
                        bib_key TEXT PRIMARY KEY,
                        content TEXT NOT NULL,
                        source_file_path TEXT NOT NULL
                    );
                    CREATE TABLE guideline_bib_links(
                        guideline_id TEXT NOT NULL,
                        bib_key TEXT NOT NULL,
                        PRIMARY KEY(guideline_id, bib_key)
                    );
                    """
                )
                connection.execute(
                    """
                    INSERT INTO guideline_records(
                        guideline_id, title, source_file_path, quality_label,
                        metadata_json, export_topic, source_revision, source_hash, ingested_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "gui_ABC123",
                        "My Guideline",
                        "coding-guidelines/expressions/gui_ABC123.rst",
                        "mixed",
                        '{"export_filename":"gui_ABC123.rst"}',
                        "expressions",
                        "rev",
                        "hash",
                        "now",
                    ),
                )
                connection.execute(
                    "INSERT INTO guideline_blocks(block_id, guideline_id, block_type, order_index, content) VALUES(?, ?, ?, ?, ?)",
                    ("gui_ABC123:body:1", "gui_ABC123", "body", 1, "Example body content"),
                )
                connection.commit()
            finally:
                connection.close()

            summary = export_guidelines(db_path=db_path, output_root=output_root)
            self.assertGreater(int(summary["file_count"]), 0)

            exported_path = output_root / "expressions" / "gui_ABC123.rst"
            self.assertTrue(exported_path.exists())
            index_path = output_root / "expressions" / "index.rst"
            self.assertTrue(index_path.exists())
            index_text = index_path.read_text(encoding="utf-8")
            self.assertIn(".. toctree::", index_text)
            self.assertIn("gui_*", index_text)
            self.assertNotIn("MANAGED GUIDELINE SIDECARS", index_text)


if __name__ == "__main__":
    unittest.main()
