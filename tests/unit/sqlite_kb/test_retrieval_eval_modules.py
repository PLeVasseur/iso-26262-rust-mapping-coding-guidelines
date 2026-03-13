from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.eval.reporting import infer_root_cause_run_and_cell, write_eval_report  # noqa: E402
from retrieval.eval.runner import load_eval_prompts  # noqa: E402


class RetrievalEvalModulesTests(unittest.TestCase):
    def test_load_eval_prompts_validates_minimal_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            eval_path = Path(temp_dir) / "eval.yaml"
            eval_path.write_text(
                "\n".join(
                    [
                        "version: 1",
                        "prompts:",
                        "  - prompt_id: P1",
                        "    slice: issue_identification",
                        "    query_text: test query",
                        "    category: ergonomics",
                        "    semantic_focus: false",
                        "    modes: [lexical, semantic, hybrid]",
                        "    expected_row_markers: [1d]",
                        "    relevant_terms: [error]",
                        "    expected_item_kinds: [method]",
                        "    required_evidence_fields: [item_path]",
                        "    target_scope: any",
                    ]
                ),
                encoding="utf-8",
            )
            prompts = load_eval_prompts(eval_path)
            self.assertEqual(len(prompts), 1)
            self.assertEqual(prompts[0]["prompt_id"], "P1")

    def test_reporting_helpers_infer_and_write(self) -> None:
        report_path = (
            ROOT
            / ".cache/sqlite_kb/reports/rust_reference/root_cause/RUN123/matrix/CELLA/report.json"
        )
        run_id, cell_id = infer_root_cause_run_and_cell(report_path)
        self.assertEqual(run_id, "RUN123")
        self.assertEqual(cell_id, "CELLA")

        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "report.json"
            payload = {"summary": {"failed_cases": 0}}
            write_eval_report(out, payload)
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(written["summary"]["failed_cases"], 0)


if __name__ == "__main__":
    unittest.main()
