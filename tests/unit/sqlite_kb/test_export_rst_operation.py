from __future__ import annotations

import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path

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
            common_root = root / "scripts" / "common"
            common_root.mkdir(parents=True, exist_ok=True)
            (common_root / "guideline_templates.py").write_text(
                """
def generate_id(prefix):
    return f\"{prefix}_DUMMY\"

def guideline_rst_template(guideline_title, category, status, release_begin, release_end, fls_id, decidability, scope, tags, amplification, exceptions, rationale, non_compliant_examples, compliant_examples, bibliography_entries):
    return "\\n".join([
        f".. guideline:: {guideline_title}",
        f"   :id: {generate_id('gui')}",
        f"   :category: {category}",
        f"   :status: {status}",
        f"   :release: {release_begin}",
        f"   :fls: {fls_id}",
        f"   :decidability: {decidability}",
        f"   :scope: {scope}",
        f"   :tags: {tags}",
        "",
        f"   {amplification}",
        "",
        "   .. rationale::",
        f"      {rationale}",
    ])
""".strip()
                + "\n",
                encoding="utf-8",
            )
            (common_root / "guideline_pages.py").write_text(
                """
def build_guideline_page_content(title, guideline_body):
    return f\"{title}\\n{'=' * len(title)}\\n\\n{guideline_body}\\n\"
""".strip()
                + "\n",
                encoding="utf-8",
            )

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
                        '{"export_filename":"gui_ABC123.rst","category":"advisory","status":"draft","release":"1.85.1","fls_id":"fls_dummy12345","decidability":"undecidable","scope":"module","tags":["subset"]}',
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
                connection.execute(
                    "INSERT INTO guideline_fls_source_mappings VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (
                        "gui_ABC123",
                        "coding-guidelines/expressions/gui_ABC123.rst",
                        "fls_dummy12345",
                        1,
                        "rev",
                        "hash",
                        "now",
                    ),
                )
                connection.execute(
                    "INSERT INTO guideline_fls_resolution_overrides VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "gui_ABC123",
                        "fls_effective999",
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

            summary = export_guidelines(db_path=db_path, output_root=output_root)
            self.assertGreater(int(summary["file_count"]), 0)

            exported_path = output_root / "expressions" / "gui_ABC123.rst"
            self.assertTrue(exported_path.exists())
            exported_text = exported_path.read_text(encoding="utf-8")
            self.assertIn(".. guideline::", exported_text)
            self.assertIn(":fls: fls_effective999", exported_text)
            index_path = output_root / "expressions" / "index.rst"
            self.assertTrue(index_path.exists())
            index_text = index_path.read_text(encoding="utf-8")
            self.assertIn(".. toctree::", index_text)
            self.assertIn("gui_*", index_text)
            self.assertNotIn("MANAGED GUIDELINE SIDECARS", index_text)


if __name__ == "__main__":
    unittest.main()
