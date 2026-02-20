from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
THIS_DIR = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from _fixture import create_reference_fixture  # noqa: E402

from sqlite_build_rust_reference import (  # noqa: E402
    DEFAULT_EXTRACTOR_DB,
    DEFAULT_TABLE_NODE_ID,
    build_rust_reference_db,
)
from sqlite_verify_rust_reference_query_set import verify_query_suite  # noqa: E402


class QuerySetVerifierTests(unittest.TestCase):
    def _build_fixture_db(self, temp_root: Path) -> Path:
        db_path = temp_root / "current" / "rust_reference.sqlite"
        snapshot_root = temp_root / "snapshots"
        manifest_path = temp_root / "manifest.yaml"
        reference_source_dir = create_reference_fixture(temp_root)

        build_rust_reference_db(
            db_path=db_path,
            snapshot_root=snapshot_root,
            manifest_path=manifest_path,
            extractor_db=DEFAULT_EXTRACTOR_DB,
            table_node_id=DEFAULT_TABLE_NODE_ID,
            reference_source_dir=reference_source_dir,
            reference_revision="fixture-001",
            min_sections=4,
            min_statements=8,
            min_mechanisms=4,
        )
        return db_path

    def test_verifier_reports_pass_and_fail_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = self._build_fixture_db(temp_root)
            contract_path = ROOT / "config" / "sqlite_query_contracts" / "rust_reference.yaml"
            query_log_root = temp_root / "query_logs"

            query_cases = [
                {
                    "case_id": "PASS-ROW-1A",
                    "row_marker": "1a",
                    "query_type": "row_verdict",
                    "query_id": "row_verdicts_for_table1",
                    "params": {},
                    "purpose": "pass case",
                }
            ]
            expected_pass = {
                "PASS-ROW-1A": {
                    "expectation_type": "row_verdict",
                    "expected_verdict": "applicable",
                }
            }
            pass_report = verify_query_suite(
                db_path=db_path,
                contract_path=contract_path,
                query_cases=query_cases,
                expected_cases=expected_pass,
                query_log_root=query_log_root,
            )
            self.assertEqual(pass_report["summary"]["failed_cases"], 0)
            self.assertFalse(pass_report["remediation_required"])

            expected_fail = {
                "PASS-ROW-1A": {
                    "expectation_type": "row_verdict",
                    "expected_verdict": "not_applicable",
                }
            }
            fail_report = verify_query_suite(
                db_path=db_path,
                contract_path=contract_path,
                query_cases=query_cases,
                expected_cases=expected_fail,
                query_log_root=query_log_root,
            )
            self.assertGreaterEqual(fail_report["summary"]["failed_cases"], 1)
            self.assertTrue(fail_report["remediation_required"])


if __name__ == "__main__":
    unittest.main()
