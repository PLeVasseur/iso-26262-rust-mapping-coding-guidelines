from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from controller_decision import build_decision_packet, resolve_candidate_selection  # noqa: E402


def write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=False)


class ControllerDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = os.environ.get("CONTROLLER_DECISION_COMMAND")
        if "CONTROLLER_DECISION_COMMAND" in os.environ:
            del os.environ["CONTROLLER_DECISION_COMMAND"]

    def tearDown(self) -> None:
        if self._env_backup is None:
            os.environ.pop("CONTROLLER_DECISION_COMMAND", None)
        else:
            os.environ["CONTROLLER_DECISION_COMMAND"] = self._env_backup

    def _packet(self) -> dict:
        observation = {
            "runtime_failures": 0,
            "policy_failures": 0,
            "iso_obligation_gap_count": 1,
            "total_deficit_count": 1,
            "deficits": [
                {
                    "deficit_id": "d-1",
                    "type": "quality_gap",
                    "severity": "high",
                    "guideline_id": "RG-1",
                    "target_id": "",
                    "distance_to_pass": 1,
                }
            ],
        }
        candidates = [
            {
                "candidate_id": "cand-1",
                "actions": [{"type": "rewrite_rule_statement_specific", "guideline_id": "RG-1"}],
                "pre_score": 10,
                "risk_penalty": 0.1,
                "mutation_footprint_estimate": 1,
                "bundle_signature": "sig-1",
                "expected_lane_deltas": {"quality": 1},
            },
            {
                "candidate_id": "cand-2",
                "actions": [{"type": "upgrade_examples_non_placeholder", "guideline_id": "RG-1"}],
                "pre_score": 9,
                "risk_penalty": 0.2,
                "mutation_footprint_estimate": 1,
                "bundle_signature": "sig-2",
                "expected_lane_deltas": {"examples": 1},
            },
        ]
        return build_decision_packet(
            session_id="test-session",
            iteration=1,
            observation=observation,
            candidates=candidates,
            suppressed_signatures=set(),
            historical_signatures=set(),
            alignment_overrides={},
            policy_context={"beam_width": 4},
        )

    def test_resolve_selection_deterministic_when_llm_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            policy_path = temp_root / "policy.yaml"
            write_yaml(
                policy_path,
                {
                    "version": 1,
                    "enabled": True,
                    "max_selected_candidates": 1,
                    "llm": {
                        "enabled": False,
                        "fallback_to_deterministic": True,
                        "command": [],
                    },
                },
            )
            iteration_dir = temp_root / "iteration"
            result = resolve_candidate_selection(
                ROOT,
                self._packet(),
                iteration_dir,
                policy_path=policy_path,
            )
            self.assertEqual(result["selection_source"], "deterministic")
            self.assertEqual(result["ordered_candidate_ids"], ["cand-1"])

    def test_resolve_selection_uses_llm_valid_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            decision = {
                "selected_candidate_ids": ["cand-2"],
                "rejected_candidate_ids": ["cand-1"],
                "rationale": "Prefer example-focused delta first",
                "risk_notes": ["Lower mutation risk"],
                "confidence": "high",
                "fallback_recommended": False,
            }
            command_code = f"import json; print({json.dumps(json.dumps(decision))})"
            policy_path = temp_root / "policy.yaml"
            write_yaml(
                policy_path,
                {
                    "version": 1,
                    "enabled": True,
                    "max_selected_candidates": 2,
                    "llm": {
                        "enabled": True,
                        "fallback_to_deterministic": True,
                        "command": [sys.executable, "-c", command_code],
                    },
                },
            )
            iteration_dir = temp_root / "iteration"
            result = resolve_candidate_selection(
                ROOT,
                self._packet(),
                iteration_dir,
                policy_path=policy_path,
            )
            self.assertEqual(result["selection_source"], "llm")
            self.assertEqual(result["ordered_candidate_ids"], ["cand-2"])
            self.assertTrue((iteration_dir / "llm_decision.raw.json").exists())
            self.assertTrue((iteration_dir / "llm_decision.validated.json").exists())

    def test_resolve_selection_falls_back_on_unknown_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            decision = {
                "selected_candidate_ids": ["cand-99"],
                "rationale": "Unknown candidate",
                "risk_notes": [],
                "confidence": "low",
                "fallback_recommended": False,
            }
            command_code = f"import json; print({json.dumps(json.dumps(decision))})"
            policy_path = temp_root / "policy.yaml"
            write_yaml(
                policy_path,
                {
                    "version": 1,
                    "enabled": True,
                    "max_selected_candidates": 2,
                    "llm": {
                        "enabled": True,
                        "fallback_to_deterministic": True,
                        "command": [sys.executable, "-c", command_code],
                    },
                },
            )
            iteration_dir = temp_root / "iteration"
            result = resolve_candidate_selection(
                ROOT,
                self._packet(),
                iteration_dir,
                policy_path=policy_path,
            )
            self.assertEqual(result["selection_source"], "fallback")
            self.assertEqual(result["resolution_reason"], "llm_selected_unknown_candidates")
            self.assertEqual(result["ordered_candidate_ids"], ["cand-1", "cand-2"])


if __name__ == "__main__":
    unittest.main()
