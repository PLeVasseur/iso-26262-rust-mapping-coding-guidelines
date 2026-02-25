from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.eval.weak_prompt_manifest import build_weak_prompt_manifest  # noqa: E402


class EvalWeakPromptManifestTests(unittest.TestCase):
    def test_build_manifest_ranks_prompts_by_composite_risk(self) -> None:
        eval_payload = {
            "cases": [
                {
                    "prompt_id": "P1",
                    "mode": "hybrid",
                    "mrr_at_k": 0.2,
                    "row_hit_rate": 0.2,
                    "slice": "issue_identification",
                    "expect_abstain": False,
                },
                {
                    "prompt_id": "P2",
                    "mode": "hybrid",
                    "mrr_at_k": 0.7,
                    "row_hit_rate": 0.8,
                    "slice": "issue_identification",
                    "expect_abstain": False,
                },
            ]
        }
        comparator_payload = {
            "cases": [
                {"prompt_id": "P1", "mode": "hybrid", "mrr_at_k": 0.8, "row_hit_rate": 0.9},
                {"prompt_id": "P2", "mode": "hybrid", "mrr_at_k": 0.75, "row_hit_rate": 0.85},
            ]
        }

        manifest = build_weak_prompt_manifest(
            eval_payload=eval_payload,
            comparator_payload=comparator_payload,
            top_n=2,
        )
        self.assertEqual(manifest.get("schema_version"), 1)
        self.assertEqual(list(manifest.get("weak_prompt_ids", []))[0], "P1")
        self.assertEqual(manifest.get("scoring", {}).get("primary_mode"), "hybrid")


if __name__ == "__main__":
    unittest.main()
