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

from retrieval.operations.eval import load_eval_prompts  # noqa: E402


class RetrievalEvalLoaderTests(unittest.TestCase):
    def test_retrieval_eval_prompt_file_has_required_shape(self) -> None:
        eval_path = ROOT / "data" / "query_testsets" / "rust_reference_table1_retrieval_eval.yaml"
        prompts = load_eval_prompts(eval_path)

        self.assertGreaterEqual(len(prompts), 20)
        slices = {prompt["slice"] for prompt in prompts}
        self.assertEqual(slices, {"issue_identification", "resolution_identification"})

        for prompt in prompts:
            self.assertTrue(prompt["prompt_id"])
            self.assertTrue(prompt["query_text"])
            self.assertGreaterEqual(len(prompt["modes"]), 1)
            self.assertIn("lexical", prompt["modes"])
            self.assertIn("semantic", prompt["modes"])
            self.assertIn("hybrid", prompt["modes"])

    def test_loader_accepts_optional_min_metrics_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            eval_path = Path(temp_dir) / "eval.yaml"
            eval_path.write_text(
                """
version: 1
suite_id: core_docs_test_suite
prompts:
  - prompt_id: TEST-MIN
    category: ergonomics
    slice: issue_identification
    query_text: defensive query
    modes: [lexical]
    expected_row_markers: [1d]
    expected_item_kinds: [method]
    required_evidence_fields: [item_path]
    target_scope: any
    min_metrics:
      lexical:
        mrr_at_k: 0.8
                """.strip()
                + "\n",
                encoding="utf-8",
            )

            prompts = load_eval_prompts(eval_path)

        self.assertEqual(len(prompts), 1)
        self.assertIn("lexical", prompts[0]["min_metrics"])
        self.assertEqual(prompts[0]["min_metrics"]["lexical"]["mrr_at_k"], 0.8)
        self.assertEqual(prompts[0]["target_scope"], "any")

    def test_core_docs_prompt_file_meets_metadata_contract(self) -> None:
        eval_path = ROOT / "data" / "query_testsets" / "core_docs_table1_retrieval_eval.yaml"
        prompts = load_eval_prompts(eval_path)

        self.assertGreaterEqual(len(prompts), 25)
        categories = [prompt.get("category") for prompt in prompts]
        self.assertGreaterEqual(sum(1 for category in categories if category == "ergonomics"), 5)
        self.assertGreaterEqual(sum(1 for category in categories if category == "safety_panic"), 5)
        self.assertGreaterEqual(sum(1 for category in categories if category == "concurrency"), 5)
        self.assertGreaterEqual(sum(1 for category in categories if category == "traits"), 5)

        abstain_count = sum(1 for prompt in prompts if prompt.get("expect_abstain"))
        self.assertGreaterEqual(abstain_count, max(1, int(0.2 * len(prompts))))

        for prompt in prompts:
            self.assertTrue(prompt.get("expected_item_kinds"))
            self.assertTrue(prompt.get("required_evidence_fields"))
            self.assertIn(prompt.get("target_scope"), {"any", "qnx", "vxworks", "embedded"})

    def test_loader_rejects_prompt_missing_required_metadata_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            eval_path = Path(temp_dir) / "eval.yaml"
            eval_path.write_text(
                """
version: 1
suite_id: core_docs_test_suite
prompts:
  - prompt_id: TEST-META
    slice: issue_identification
    query_text: metadata missing
    modes: [lexical]
    expected_row_markers: [1d]
    relevant_terms: [metadata]
    target_scope: any
                """.strip()
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "expected_item_kinds"):
                load_eval_prompts(eval_path)


if __name__ == "__main__":
    unittest.main()
