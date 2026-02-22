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

from sqlite_eval_rust_reference_retrieval import load_eval_prompts  # noqa: E402


class RetrievalEvalLoaderTests(unittest.TestCase):
    def test_retrieval_eval_prompt_file_has_required_shape(self) -> None:
        eval_path = (
            ROOT / "data" / "query_testsets" / "rust_reference_table1_retrieval_eval.yaml"
        )
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
prompts:
  - prompt_id: TEST-MIN
    slice: issue_identification
    query_text: defensive query
    modes: [lexical]
    expected_row_markers: [1d]
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


if __name__ == "__main__":
    unittest.main()
