from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
THIS_DIR = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from sqlite_verify_rust_reference_query_set import (  # noqa: E402
    load_expected_cases,
    load_query_suite,
    validate_suite_shapes,
)


class QuerySetLoaderTests(unittest.TestCase):
    def test_default_query_set_has_45_cases_and_complete_expected_mapping(self) -> None:
        queries_path = ROOT / "data" / "query_testsets" / "rust_reference_table1_queries.yaml"
        expected_path = ROOT / "data" / "query_testsets" / "rust_reference_table1_expected.yaml"

        query_cases = load_query_suite(queries_path)
        expected_cases = load_expected_cases(expected_path)
        validate_suite_shapes(
            query_cases=query_cases, expected_cases=expected_cases, expected_case_count=45
        )

        self.assertEqual(len(query_cases), 45)
        markers = [case["row_marker"] for case in query_cases]
        for marker in [f"1{chr(ord('a') + idx)}" for idx in range(9)]:
            self.assertEqual(markers.count(marker), 5)


if __name__ == "__main__":
    unittest.main()
