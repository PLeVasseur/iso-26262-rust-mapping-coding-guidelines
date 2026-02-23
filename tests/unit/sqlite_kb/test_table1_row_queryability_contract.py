from __future__ import annotations

import sqlite3
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
from sqlite_query_guardrails import execute_contract_query  # noqa: E402


class Table1RowQueryabilityContractTests(unittest.TestCase):
    def test_all_rows_have_allowed_verdict_and_mechanism_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "current" / "rust_reference.sqlite"
            snapshot_root = temp_root / "snapshots"
            manifest_path = temp_root / "manifest.yaml"
            query_log_root = temp_root / "query_logs"
            contract_path = ROOT / "config" / "sqlite_query_contracts" / "rust_reference.yaml"
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

            verdict_result = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="row_verdicts_for_table1",
                params={},
                query_log_root=query_log_root,
            )

            rows = verdict_result["rows"]
            self.assertEqual(len(rows), 9)
            expected = {f"1{chr(ord('a') + idx)}" for idx in range(9)}
            self.assertEqual({row["row_marker"] for row in rows}, expected)

            for row in rows:
                verdict = row["verdict"]
                self.assertIn(verdict, {"applicable", "not_applicable"})

                connection = sqlite3.connect(db_path)
                try:
                    req_len = int(
                        connection.execute(
                            "SELECT LENGTH(requirement_text) "
                            "FROM table1_rows WHERE row_node_id = ?",
                            (row["row_node_id"],),
                        ).fetchone()[0]
                    )
                    profile_terms = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM table1_row_profile_terms WHERE row_node_id = ?",
                            (row["row_node_id"],),
                        ).fetchone()[0]
                    )
                finally:
                    connection.close()

                self.assertGreaterEqual(req_len, 48)
                self.assertLessEqual(req_len, 480)
                self.assertGreaterEqual(profile_terms, 3)

                if verdict == "not_applicable":
                    self.assertTrue(str(row["rationale"]).strip())
                    self.assertTrue(str(row["source_anchor"]).strip())
                    self.assertTrue(str(row["rationale_timestamp"]).strip())
                    continue

                self.assertTrue(str(row["rationale_timestamp"]).strip())

                mechanism_result = execute_contract_query(
                    db_path=db_path,
                    contract_path=contract_path,
                    query_id="mechanisms_for_row",
                    params={"row_node_id": row["row_node_id"]},
                    query_log_root=query_log_root,
                )
                self.assertGreaterEqual(mechanism_result["row_count"], 1)
                for mechanism in mechanism_result["rows"]:
                    self.assertTrue(str(mechanism["source_fetched_at"]).strip())


if __name__ == "__main__":
    unittest.main()
