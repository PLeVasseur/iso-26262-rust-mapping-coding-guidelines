from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_guideline_examples import (  # noqa: E402
    classify_rustdoc_observed_outcome,
    has_assertion,
    infer_expected_outcome,
    normalize_outcome,
)
from controller_actions import (  # noqa: E402
    _compile_expectation_for_outcome,
    _default_example_outcome,
)
from controller_observe import collect_example_deficits  # noqa: E402


class ExampleOutcomeLogicTests(unittest.TestCase):
    def test_normalize_outcome_aliases(self) -> None:
        self.assertEqual(normalize_outcome("documented-only"), "documented_only")
        self.assertEqual(normalize_outcome(" runtime_panic "), "runtime_panic")

    def test_infer_expected_outcome(self) -> None:
        self.assertEqual(
            infer_expected_outcome(
                "compliant", "compile_pass", "possible-with-clippy", "decidable"
            ),
            "assertion_pass",
        )
        self.assertEqual(
            infer_expected_outcome(
                "non_compliant", "compile_pass", "possible-with-clippy", "decidable"
            ),
            "runtime_panic",
        )
        self.assertEqual(
            infer_expected_outcome("non_compliant", "compile_pass", "clippy", "decidable"),
            "lint_trigger",
        )
        self.assertEqual(
            infer_expected_outcome("non_compliant", "compile_fail", "compiler", "decidable"),
            "compile_fail",
        )

    def test_has_assertion(self) -> None:
        self.assertTrue(has_assertion("fn main() { assert_eq!(1 + 1, 2); }"))
        self.assertFalse(has_assertion("fn main() { let value = 1 + 1; let _ = value; }"))

    def test_classify_rustdoc_observed_outcome(self) -> None:
        self.assertEqual(
            classify_rustdoc_observed_outcome("compile_fail", "compile_fail", 1, "error[E0308]"),
            "compile_fail",
        )
        self.assertEqual(
            classify_rustdoc_observed_outcome(
                "runtime_panic", "should_panic", 0, "test result: ok"
            ),
            "runtime_panic",
        )
        self.assertEqual(
            classify_rustdoc_observed_outcome("assertion_pass", "rust", 0, "test result: ok"),
            "assertion_pass",
        )

    def test_compile_expectation_mapping(self) -> None:
        self.assertEqual(
            _compile_expectation_for_outcome("assertion_pass", "compliant"), "compile_pass"
        )
        self.assertEqual(
            _compile_expectation_for_outcome("compile_fail", "non_compliant"), "compile_fail"
        )
        self.assertEqual(
            _compile_expectation_for_outcome("documented_only", "compliant"),
            "documented-only",
        )

    def test_default_example_outcome_mapping(self) -> None:
        compiler_guideline = {"decidable": "decidable", "decidable_status": "compiler"}
        self.assertEqual(
            _default_example_outcome(compiler_guideline, "non_compliant"), "compile_fail"
        )
        self.assertEqual(
            _default_example_outcome(compiler_guideline, "compliant"), "assertion_pass"
        )

        clippy_guideline = {"decidable": "decidable", "decidable_status": "clippy"}
        self.assertEqual(
            _default_example_outcome(clippy_guideline, "non_compliant"), "lint_trigger"
        )

        candidate_guideline = {
            "decidable": "decidable",
            "decidable_status": "possible-with-clippy",
        }
        self.assertEqual(
            _default_example_outcome(candidate_guideline, "non_compliant"), "runtime_panic"
        )

    def test_collect_example_deficits_emits_new_gap_types(self) -> None:
        report = {
            "errors": [],
            "example_results": [
                {
                    "guideline_id": "RG-TEST1",
                    "side": "compliant",
                    "expected_outcome": "assertion_pass",
                    "observed_outcome": "assertion_pass",
                    "outcome_match": True,
                    "assertion_present": False,
                    "negative_evidence_strong": False,
                },
                {
                    "guideline_id": "RG-TEST2",
                    "side": "non_compliant",
                    "expected_outcome": "compile_fail",
                    "observed_outcome": "assertion_pass",
                    "outcome_match": False,
                    "assertion_present": False,
                    "negative_evidence_strong": False,
                },
            ],
            "diversity_violations": [
                {
                    "count": 6,
                    "example_keys": ["RG-TEST2:non_compliant", "RG-TEST3:non_compliant"],
                }
            ],
        }

        deficits = collect_example_deficits(report)
        types = {item["type"] for item in deficits}
        self.assertIn("example_assertion_gap", types)
        self.assertIn("example_outcome_gap", types)
        self.assertIn("example_negative_evidence_gap", types)
        self.assertIn("example_diversity_gap", types)


if __name__ == "__main__":
    unittest.main()
