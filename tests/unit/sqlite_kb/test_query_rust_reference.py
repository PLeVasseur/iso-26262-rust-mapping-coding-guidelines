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
from sqlite_query_guardrails import GuardrailError, execute_contract_query  # noqa: E402


class QueryRustReferenceTests(unittest.TestCase):
    def test_row_verdicts_query_returns_all_markers(self) -> None:
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

            result = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="row_verdicts_for_table1",
                params={},
                query_log_root=query_log_root,
            )
            markers = {row["row_marker"] for row in result["rows"]}
            expected = {f"1{chr(ord('a') + idx)}" for idx in range(9)}

            self.assertEqual(result["row_count"], 9)
            self.assertEqual(markers, expected)
            self.assertTrue(all(str(row["rationale_timestamp"]).strip() for row in result["rows"]))

            metadata = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="snapshot_metadata",
                params={},
                query_log_root=query_log_root,
            )
            self.assertEqual(metadata["row_count"], 1)
            self.assertTrue(str(metadata["rows"][0]["fetched_at"]).strip())

            coverage = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="document_timestamp_coverage",
                params={},
                query_log_root=query_log_root,
            )
            self.assertEqual(coverage["row_count"], 1)
            row = coverage["rows"][0]
            self.assertEqual(int(row["missing_fetched_at"]), 0)
            self.assertEqual(int(row["missing_commit_sha"]), 0)

    def test_guardrails_reject_forbidden_write_sql(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "current" / "rust_reference.sqlite"
            snapshot_root = temp_root / "snapshots"
            manifest_path = temp_root / "manifest.yaml"
            bad_contract = temp_root / "bad_contract.yaml"
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

            bad_contract.write_text(
                """
version: 1
database: rust_reference
queries:
  illegal_write:
    params: []
    row_limit: 10
    sql: |
      DELETE FROM table1_rows
                """.strip(),
                encoding="utf-8",
            )

            with self.assertRaises(GuardrailError):
                execute_contract_query(
                    db_path=db_path,
                    contract_path=bad_contract,
                    query_id="illegal_write",
                    params={},
                )


if __name__ == "__main__":
    unittest.main()
