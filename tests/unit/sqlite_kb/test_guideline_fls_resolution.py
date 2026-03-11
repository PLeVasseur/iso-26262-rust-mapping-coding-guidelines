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

from reconcile_guideline_fls_mappings import (  # noqa: E402
    sync_from_ws7_audit,
    update_override,
    write_report,
)
from retrieval.services.guideline_fls_resolution import (  # noqa: E402
    get_effective_guideline_fls,
    get_guideline_fls_resolution_state,
)


class GuidelineFlsResolutionTests(unittest.TestCase):
    def _build_guidelines_db(self, db_path: Path) -> None:
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
                CREATE TABLE guideline_fls_resolution_candidates(
                    audit_run_id TEXT NOT NULL,
                    guideline_id TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    paragraph_id TEXT NOT NULL,
                    document_link TEXT NOT NULL,
                    section_link TEXT NOT NULL,
                    candidate_source TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    PRIMARY KEY (audit_run_id, guideline_id, rank, paragraph_id)
                );
                CREATE TABLE guideline_fls_resolution_history(
                    history_id TEXT PRIMARY KEY,
                    guideline_id TEXT NOT NULL,
                    effective_fls_id TEXT NOT NULL,
                    resolution_kind TEXT NOT NULL,
                    resolution_status TEXT NOT NULL,
                    audit_run_id TEXT NOT NULL,
                    evidence_source_id TEXT NOT NULL,
                    rationale_text TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    approved_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO guideline_records VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "gui_dead",
                    "Dead mapping",
                    "src/coding-guidelines/associated-items/gui_dead.rst",
                    "mixed",
                    '{"fls":"fls_dead"}',
                    "associated-items",
                    "rev",
                    "hash",
                    "now",
                ),
            )
            connection.execute(
                "INSERT INTO guideline_fls_source_mappings VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    "gui_dead",
                    "src/coding-guidelines/associated-items/gui_dead.rst",
                    "fls_dead",
                    1,
                    "rev",
                    "hash",
                    "now",
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def _build_fls_db(self, db_path: Path) -> None:
        connection = sqlite3.connect(db_path)
        try:
            connection.executescript(
                """
                CREATE TABLE ws7_mapping_audit_runs(
                    run_id TEXT PRIMARY KEY,
                    generated_at TEXT,
                    fls_db_path TEXT,
                    guidelines_root TEXT,
                    heldout_manifest_path TEXT,
                    publishability_audit_path TEXT,
                    classification_counts_json TEXT
                );
                CREATE TABLE ws7_mapping_audit_rows(
                    run_id TEXT,
                    source_id TEXT,
                    source_kind TEXT,
                    target_id TEXT,
                    title TEXT,
                    rst_path TEXT,
                    source_fls_id TEXT,
                    raw_source_fls_id TEXT,
                    effective_source_fls_id TEXT,
                    resolution_kind TEXT,
                    resolution_status TEXT,
                    resolution_rationale TEXT,
                    mapping_state_source TEXT,
                    source_fls_exists INTEGER,
                    source_fls_semantic_plausibility INTEGER,
                    source_fls_overlap_tokens_json TEXT,
                    nearest_candidate_paragraphs_json TEXT,
                    best_runtime_paragraph_id TEXT,
                    classification TEXT,
                    cluster TEXT,
                    acceptable_ids_json TEXT,
                    evidence_json TEXT,
                    PRIMARY KEY (run_id, source_id)
                );
                """
            )
            connection.execute(
                "INSERT INTO ws7_mapping_audit_runs VALUES(?, ?, ?, ?, ?, ?, ?)",
                ("run-1", "now", str(db_path), "root", "heldout", "publish", "{}"),
            )
            connection.execute(
                (
                    "INSERT INTO ws7_mapping_audit_rows VALUES("
                    "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"
                    ")"
                ),
                (
                    "run-1",
                    "ws7-heldout::gui_dead",
                    "heldout_guideline",
                    "ws7-heldout::gui_dead",
                    "Dead mapping",
                    "src/coding-guidelines/associated-items/gui_dead.rst",
                    "fls_dead",
                    "fls_dead",
                    "fls_dead",
                    "",
                    "",
                    "",
                    "raw",
                    0,
                    0,
                    "[]",
                    json.dumps(
                        [
                            {
                                "paragraph_id": "fls_live",
                                "document_link": "doc.html",
                                "section_link": "doc.html#sec",
                            }
                        ]
                    ),
                    "",
                    "stale_mapping",
                    "corpus_gap_staleness",
                    "[]",
                    json.dumps({"rationale": "dead raw id"}),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_sync_and_approve_unresolved_updates_effective_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fls_db = temp_root / "fls_spec.db"
            guidelines_db = temp_root / "guidelines_repo.sqlite"
            self._build_fls_db(fls_db)
            self._build_guidelines_db(guidelines_db)

            sync_payload = sync_from_ws7_audit(
                fls_db_path=fls_db,
                guidelines_db_path=guidelines_db,
            )
            self.assertEqual(sync_payload["synced_guidelines"], 1)
            proposed = get_guideline_fls_resolution_state("gui_dead", db_path=guidelines_db)
            self.assertEqual(proposed["resolution_status"], "proposed")
            self.assertEqual(proposed["effective_fls_id"], "fls_dead")

            update_override(
                guidelines_db_path=guidelines_db,
                guideline_id="gui_dead",
                effective_fls_id="fls_UNRESOLVED",
                resolution_kind="unresolved_expected",
                resolution_status="approved",
                rationale_text="dead mapping, no convincing replacement",
                approved_by="tester",
            )
            state = get_guideline_fls_resolution_state("gui_dead", db_path=guidelines_db)
            self.assertEqual(state["effective_fls_id"], "fls_UNRESOLVED")
            self.assertEqual(state["mapping_state_source"], "override")
            self.assertEqual(
                get_effective_guideline_fls("gui_dead", db_path=guidelines_db), "fls_UNRESOLVED"
            )

            report_path = temp_root / "report.json"
            report = write_report(guidelines_db_path=guidelines_db, output_path=report_path)
            self.assertEqual(report["row_count"], 1)
            self.assertTrue(report_path.is_file())


if __name__ == "__main__":
    unittest.main()
