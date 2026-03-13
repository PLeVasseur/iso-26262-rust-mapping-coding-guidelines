from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
THIS_DIR = Path(__file__).resolve().parent
TESTS_UNIT = ROOT / "tests" / "unit"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
if str(TESTS_UNIT) not in sys.path:
    sys.path.insert(0, str(TESTS_UNIT))

from _fixture import create_reference_fixture  # noqa: E402
from retrieval.build.reports import validate_chunk_first_db  # noqa: E402
from retrieval.operations.build import (  # noqa: E402
    DEFAULT_EXTRACTOR_DB,
    DEFAULT_TABLE_NODE_ID,
    build_rust_reference_db,
)
from scripts.build_fls_db import build_fls_db  # noqa: E402
from test_fls_step6 import (  # noqa: E402
    _write_paragraph_ids,
    _write_sample_fls_source,
    _write_spec_lock,
)
from test_query_core_docs import QueryCoreDocsTests  # noqa: E402


class ChunkFirstSharedReportsTests(unittest.TestCase):
    def test_validate_chunk_first_db_supports_rust_reference_core_docs_and_fls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)

            rust_db = temp_root / "current" / "rust_reference.sqlite"
            rust_summary = build_rust_reference_db(
                db_path=rust_db,
                snapshot_root=temp_root / "snapshots",
                manifest_path=temp_root / "manifest.yaml",
                extractor_db=DEFAULT_EXTRACTOR_DB,
                table_node_id=DEFAULT_TABLE_NODE_ID,
                reference_source_dir=create_reference_fixture(temp_root / "rust_fixture"),
                reference_revision="fixture-001",
                min_sections=4,
                min_statements=8,
                min_mechanisms=4,
            )

            fls_source = temp_root / "fls_source"
            fls_db = temp_root / "fls_spec.db"
            spec_lock = temp_root / "spec.lock"
            topology = temp_root / "paragraph-ids.json"
            _write_sample_fls_source(fls_source)
            _write_spec_lock(spec_lock)
            _write_paragraph_ids(topology)
            fls_summary = build_fls_db(
                source_dir=fls_source,
                db_path=fls_db,
                spec_lock_path=spec_lock,
                topology_path=topology,
                compat_symlink_mode="never",
                report_root=temp_root / "reports" / "fls_spec",
            )

            core_docs_test = QueryCoreDocsTests()
            core_docs_fixture_root = temp_root / "core_docs_fixture"
            core_docs_db, _, _ = core_docs_test._build_fixture_db(core_docs_fixture_root)

            self.assertTrue(Path(rust_summary["chunk_first_report_path"]).is_file())
            self.assertTrue(Path(fls_summary["chunk_first_report_path"]).is_file())
            self.assertTrue(
                (
                    temp_root / "reports" / "rust_reference" / "current_chunk_first_validation.json"
                ).is_file()
            )
            self.assertTrue(
                (
                    temp_root / "reports" / "fls_spec" / "current_chunk_first_validation.json"
                ).is_file()
            )
            core_docs_reports = list(
                (core_docs_fixture_root / "reports" / "core_docs").glob(
                    "*_chunk_first_validation.json"
                )
            )
            self.assertTrue(core_docs_reports)
            self.assertTrue(
                (
                    core_docs_fixture_root
                    / "reports"
                    / "core_docs"
                    / "current_chunk_first_validation.json"
                ).is_file()
            )

            for corpus, db_path in (
                ("rust_reference", rust_db),
                ("fls_spec", fls_db),
                ("core_docs", core_docs_db),
            ):
                report = validate_chunk_first_db(db_path, corpus=corpus)
                self.assertTrue(report["passed"], report)
                self.assertEqual(report["corpus"], corpus)
                self.assertGreaterEqual(int(report["chunk_count"]), 1)
                self.assertGreaterEqual(int(report["chunks_fts_count"]), 1)
                self.assertTrue(report["chunk_fts_mapping"]["passed"], report)
                self.assertTrue(str(report["db_path"]))
                self.assertEqual(len(str(report["db_sha256"])), 64)
                self.assertGreaterEqual(int(report["schema_user_version"]), 0)

            current_report_payload = json.loads(
                (
                    temp_root / "reports" / "rust_reference" / "current_chunk_first_validation.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(current_report_payload["corpus"], "rust_reference")
            self.assertTrue(current_report_payload["passed"])
            self.assertEqual(len(current_report_payload["db_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
