from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.operations.query import resolve_row_projection_policy  # noqa: E402


class RowProjectionPolicyResolutionTests(unittest.TestCase):
    def test_policy_loads_corpus_abstain_defaults(self) -> None:
        policy = resolve_row_projection_policy(root=ROOT, corpus="rust_reference")
        self.assertAlmostEqual(policy.top_score_floor, 0.015, places=6)
        self.assertAlmostEqual(policy.margin, 0.005, places=6)
        self.assertEqual(policy.min_evidence_hits, 1)
        self.assertAlmostEqual(float(policy.thresholds.get("1a", 0.0)), 0.02, places=6)
        self.assertAlmostEqual(float(policy.thresholds.get("1d", 0.0)), 0.015, places=6)

    def test_env_overrides_apply_to_policy_resolution(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "SQLKB_ROW_PROJECTION_TOP_SCORE_FLOOR": "0.30",
                "SQLKB_ROW_PROJECTION_MARGIN": "0.08",
                "SQLKB_ROW_PROJECTION_MIN_EVIDENCE_HITS": "2",
            },
            clear=False,
        ):
            policy = resolve_row_projection_policy(root=ROOT, corpus="rust_reference")
        self.assertAlmostEqual(policy.top_score_floor, 0.30, places=6)
        self.assertAlmostEqual(policy.margin, 0.08, places=6)
        self.assertEqual(policy.min_evidence_hits, 2)


if __name__ == "__main__":
    unittest.main()
