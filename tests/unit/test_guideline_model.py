from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from _common import normalize_guideline_record  # noqa: E402
from check_guideline_examples import (  # noqa: E402
    parse_first_rust_fence,
    rustdoc_expectation_matches,
)


class GuidelineModelTests(unittest.TestCase):
    def test_normalize_legacy_decidable_aliases(self) -> None:
        record = {
            "id": "RG-ALIAS",
            "decideable": "undecideable",
            "decideable-status": "impossible-with-clippy",
        }
        normalized = normalize_guideline_record(record)
        self.assertEqual(normalized["decidable"], "undecidable")
        self.assertEqual(normalized["decidable_status"], "impossible-with-clippy")
        self.assertNotIn("decideable", normalized)
        self.assertNotIn("decideable-status", normalized)

    def test_parse_first_rust_fence(self) -> None:
        markdown = '# Example\n\n```compile_fail\nfn main() { let _: u32 = "x"; }\n```\n'
        parsed = parse_first_rust_fence(markdown)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed[0], "compile_fail")

    def test_rustdoc_expectation_matching(self) -> None:
        self.assertTrue(rustdoc_expectation_matches("compile_fail", "compile_fail"))
        self.assertTrue(rustdoc_expectation_matches("no_run", "no_run"))
        self.assertTrue(rustdoc_expectation_matches("compile_pass", "rust"))
        self.assertFalse(rustdoc_expectation_matches("compile_fail", "rust"))


if __name__ == "__main__":
    unittest.main()
