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

from validate_ws7_mapping_audit import (  # noqa: E402
    generate_mapping_audit,
    persist_mapping_audit_to_db,
    write_mapping_audit_diff,
    write_mapping_cleanup_tasks,
)


class ValidateWs7MappingAuditTests(unittest.TestCase):
    def test_generate_mapping_audit_classifies_stale_and_ranking_bug(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            guidelines_root = temp_root / "guidelines"
            rst_dir = guidelines_root / "src" / "coding-guidelines" / "expressions"
            rst_dir.mkdir(parents=True, exist_ok=True)
            stale_rst = rst_dir / "gui_stale.rst"
            stale_rst.write_text(
                "Stale Example\n============\n\n:fls: fls_missing\n\nNeeds pointer provenance.\n",
                encoding="utf-8",
            )
            ranking_rst = rst_dir / "gui_rank.rst"
            ranking_rst.write_text(
                (
                    "Pointer Example\n===============\n\n:fls: fls_live\n\n"
                    "Prefer strict provenance APIs for raw pointer manipulation.\n"
                ),
                encoding="utf-8",
            )

            manifest_path = temp_root / "heldout.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "path": "src/coding-guidelines/expressions/gui_stale.rst",
                                "acceptable_ids": ["fls_other"],
                                "provenance": {"stable_identifier": "heldout::stale"},
                            },
                            {
                                "path": "src/coding-guidelines/expressions/gui_rank.rst",
                                "acceptable_ids": ["fls_live"],
                                "provenance": {"stable_identifier": "heldout::rank"},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            publishability_audit = temp_root / "publishability.json"
            publishability_audit.write_text(json.dumps({"rows": []}), encoding="utf-8")

            guidelines_db = temp_root / "guidelines_repo.sqlite"
            guid_conn = sqlite3.connect(guidelines_db)
            try:
                guid_conn.executescript(
                    """
                    CREATE TABLE guideline_records(
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
                    CREATE TABLE guideline_fls_source_mappings(
                        guideline_id TEXT PRIMARY KEY,
                        source_file_path TEXT,
                        raw_fls_id TEXT,
                        raw_fls_present INTEGER,
                        source_revision TEXT,
                        source_hash TEXT,
                        last_ingested_at TEXT
                    );
                    CREATE TABLE guideline_fls_resolution_overrides(
                        guideline_id TEXT PRIMARY KEY,
                        effective_fls_id TEXT,
                        resolution_kind TEXT,
                        resolution_status TEXT,
                        audit_run_id TEXT,
                        evidence_source_id TEXT,
                        rationale_text TEXT,
                        approved_by TEXT,
                        approved_at TEXT,
                        updated_at TEXT
                    );
                    """
                )
                guid_conn.execute(
                    "INSERT INTO guideline_fls_source_mappings VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (
                        "gui_stale",
                        "src/coding-guidelines/expressions/gui_stale.rst",
                        "fls_missing",
                        1,
                        "rev",
                        "hash",
                        "now",
                    ),
                )
                guid_conn.execute(
                    "INSERT INTO guideline_fls_source_mappings VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (
                        "gui_rank",
                        "src/coding-guidelines/expressions/gui_rank.rst",
                        "fls_live",
                        1,
                        "rev",
                        "hash",
                        "now",
                    ),
                )
                guid_conn.execute(
                    (
                        "INSERT INTO guideline_fls_resolution_overrides VALUES("
                        "?, ?, ?, ?, ?, ?, ?, ?, ?, ?"
                        ")"
                    ),
                    (
                        "gui_stale",
                        "fls_UNRESOLVED",
                        "unresolved_expected",
                        "approved",
                        "audit",
                        "source",
                        "dead mapping",
                        "tester",
                        "now",
                        "now",
                    ),
                )
                guid_conn.commit()
            finally:
                guid_conn.close()

            db_path = temp_root / "fls_spec.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE paragraphs (
                        paragraph_id TEXT PRIMARY KEY,
                        document_link TEXT NOT NULL,
                        section_link TEXT NOT NULL,
                        clean_text TEXT NOT NULL
                    );
                    """
                )
                connection.execute(
                    "INSERT INTO paragraphs VALUES(?, ?, ?, ?)",
                    (
                        "fls_live",
                        "expressions.html",
                        "expressions.html#type-cast-expressions",
                        (
                            "Prefer strict provenance APIs for raw pointer manipulation "
                            "and address-to-pointer casts."
                        ),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            output_path = temp_root / "ws7_mapping_audit.json"
            payload = generate_mapping_audit(
                fls_db_path=db_path,
                guidelines_root=guidelines_root,
                guidelines_db_path=guidelines_db,
                output_path=output_path,
                heldout_manifest_path=manifest_path,
                publishability_audit_path=publishability_audit,
            )
            file_payload = json.loads(output_path.read_text(encoding="utf-8"))
            rows = {row["source_id"]: row for row in file_payload["rows"]}
            self.assertEqual(rows["heldout::stale"]["classification"], "corpus_gap")
            self.assertEqual(rows["heldout::rank"]["classification"], "true_ranking_bug")
            self.assertEqual(rows["heldout::stale"]["raw_source_fls_id"], "fls_missing")
            self.assertEqual(rows["heldout::stale"]["effective_source_fls_id"], "fls_UNRESOLVED")
            self.assertEqual(rows["heldout::stale"]["resolution_status"], "approved")
            cleanup_path = temp_root / "ws7_mapping_cleanup_tasks.json"
            cleanup_payload = write_mapping_cleanup_tasks(
                audit_payload=payload,
                output_path=cleanup_path,
            )
            self.assertEqual(cleanup_payload["task_count"], 0)
            diff_path = temp_root / "ws7_mapping_audit_diff.json"
            diff_payload = write_mapping_audit_diff(
                previous_payload=None,
                current_payload=payload,
                output_path=diff_path,
            )
            self.assertGreaterEqual(len(diff_payload["changed_rows"]), 1)
            persist_mapping_audit_to_db(
                fls_db_path=db_path,
                audit_payload=payload,
                cleanup_payload=cleanup_payload,
            )
            connection = sqlite3.connect(db_path)
            try:
                audit_rows = int(
                    connection.execute("SELECT COUNT(*) FROM ws7_mapping_audit_rows").fetchone()[0]
                )
                cleanup_rows = int(
                    connection.execute("SELECT COUNT(*) FROM ws7_mapping_cleanup_tasks").fetchone()[
                        0
                    ]
                )
            finally:
                connection.close()
            self.assertEqual(audit_rows, 2)
            self.assertEqual(cleanup_rows, 0)


if __name__ == "__main__":
    unittest.main()
