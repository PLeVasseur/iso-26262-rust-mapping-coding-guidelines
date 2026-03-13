from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.eval.audit_contract import validate_audit_markdown  # noqa: E402


def _sample_markdown() -> str:
    prompts = [f"P{i}" for i in range(1, 11)]
    findings = []
    for prompt in prompts:
        findings.append(
            {
                "prompt_id": prompt,
                "chunks": [
                    {
                        "chunk_uid": f"chunk::{prompt.lower()}",
                        "label": "partial",
                        "severity": "low",
                        "rationale": "Relevant but not the strongest evidence.",
                    }
                ],
            }
        )

    payload = {
        "schema_version": 1,
        "phase": "ws1",
        "candidate_id": "cand-01",
        "comparator_candidate_id": "cand-00",
        "weak_prompt_ids": prompts,
        "findings": findings,
        "summary": {
            "high_count": 0,
            "medium_count": 0,
            "low_count": 10,
            "citation_readiness_delta": -0.02,
            "recommendation": "hold",
        },
    }
    import json

    return "# Audit\n\n```json\n" + json.dumps(payload, indent=2) + "\n```\n"


class SubagentAuditContractTests(unittest.TestCase):
    def test_validate_accepts_complete_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "audit.md"
            report_path.write_text(_sample_markdown(), encoding="utf-8")
            errors = validate_audit_markdown(report_path)
            self.assertEqual(errors, [])

    def test_validate_rejects_missing_prompt_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "audit.md"
            text = _sample_markdown().replace('"prompt_id": "P10"', '"prompt_id": "PXX"', 1)
            report_path.write_text(text, encoding="utf-8")
            errors = validate_audit_markdown(report_path)
            self.assertTrue(
                any("missing finding entry for weak prompt P10" in error for error in errors)
            )


if __name__ == "__main__":
    unittest.main()
