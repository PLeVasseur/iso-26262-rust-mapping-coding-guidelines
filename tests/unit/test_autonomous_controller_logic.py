from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from autonomous_controller import (  # noqa: E402
    alignment_overrides_for_iteration,
    recommend_handoff_status,
)
from controller_actions import (  # noqa: E402
    apply_add_alignment_citation_signals,
    apply_rewrite_rule_statement_specific,
    apply_spawn_rule_for_obligation_unit,
    generate_candidates,
)
from controller_scoring import improves, regression_flags  # noqa: E402


def write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=False)


class AutonomousControllerLogicTests(unittest.TestCase):
    def test_improves_prefers_smaller_gap_vector(self) -> None:
        before = {
            "runtime_failures": 0,
            "policy_failures": 0,
            "iso_obligation_gap_count": 1,
            "traceability_gap_count": 0,
            "target_fanout_gap_count": 3,
            "fls_span_gap_count": 2,
            "fls_chapter_gap_count": 0,
            "quality_gap_count": 5,
            "placeholder_gap_count": 2,
            "example_gap_count": 1,
            "iso_obligation_coverage": 0.8,
            "fls_chapter_coverage": 0.6,
            "quality_pass_ratio": 0.2,
        }
        after = {
            **before,
            "iso_obligation_gap_count": 0,
            "target_fanout_gap_count": 2,
            "quality_gap_count": 3,
            "iso_obligation_coverage": 1.0,
            "quality_pass_ratio": 0.4,
        }
        self.assertTrue(improves(before, after))

    def test_regression_flags_detect_new_gap(self) -> None:
        before = {
            "runtime_failures": 0,
            "policy_failures": 0,
            "iso_obligation_gap_count": 0,
            "traceability_gap_count": 0,
            "target_fanout_gap_count": 0,
            "fls_span_gap_count": 0,
            "fls_chapter_gap_count": 0,
            "quality_gap_count": 0,
            "placeholder_gap_count": 0,
            "example_gap_count": 0,
            "iso_obligation_coverage": 1.0,
            "fls_chapter_coverage": 1.0,
            "quality_pass_ratio": 1.0,
        }
        after = {**before, "quality_gap_count": 1}
        flags = regression_flags(before, after)
        self.assertIn("regressed_quality_gap_count", flags)

    def test_generate_candidates_deterministic(self) -> None:
        observation = {
            "deficits": [
                {
                    "deficit_id": "iso-obligation:obl-1",
                    "type": "iso_obligation_gap",
                    "severity": "critical",
                    "guideline_id": "",
                    "target_id": "T-1",
                    "obligation_unit_id": "obl-1",
                },
                {
                    "deficit_id": "quality:RG-1",
                    "type": "quality_gap",
                    "severity": "medium",
                    "guideline_id": "RG-1",
                    "target_id": "",
                    "obligation_unit_id": "",
                },
            ]
        }
        first = generate_candidates(observation, beam_width=3)
        second = generate_candidates(observation, beam_width=3)
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 1)

    def test_generate_candidates_includes_multi_action_bundle(self) -> None:
        observation = {
            "deficits": [
                {
                    "deficit_id": "fanout:T-1",
                    "type": "target_fanout_gap",
                    "severity": "high",
                    "guideline_id": "",
                    "target_id": "T-1",
                    "obligation_unit_id": "O-1",
                },
                {
                    "deficit_id": "fls:T-1",
                    "type": "fls_span_gap",
                    "severity": "high",
                    "guideline_id": "",
                    "target_id": "T-1",
                    "obligation_unit_id": "",
                },
            ]
        }
        candidates = generate_candidates(observation, beam_width=8, max_actions_per_bundle=3)
        self.assertTrue(any(len(candidate.get("actions", [])) > 1 for candidate in candidates))

    def test_generate_candidates_respects_suppressed_signatures(self) -> None:
        observation = {
            "deficits": [
                {
                    "deficit_id": "fanout:T-1",
                    "type": "target_fanout_gap",
                    "severity": "high",
                    "guideline_id": "",
                    "target_id": "T-1",
                    "obligation_unit_id": "O-1",
                }
            ]
        }
        first = generate_candidates(observation, beam_width=3)
        self.assertGreaterEqual(len(first), 1)
        signature = str(first[0].get("bundle_signature") or "")
        suppressed = generate_candidates(
            observation,
            beam_width=3,
            suppressed_signatures={signature},
        )
        self.assertTrue(
            all(candidate.get("bundle_signature") != signature for candidate in suppressed)
        )

    def test_generate_candidates_maps_known_good_flags_to_targeted_actions(self) -> None:
        observation = {
            "deficits": [
                {
                    "deficit_id": "known-good:RG-1:citation_coverage_low",
                    "type": "known_good_alignment_gap",
                    "severity": "high",
                    "guideline_id": "RG-1",
                    "target_id": "",
                    "obligation_unit_id": "",
                    "details": "flag=citation_coverage_low alignment_score=0.61",
                },
                {
                    "deficit_id": "known-good:RG-2:benchmark_similarity_gap",
                    "type": "known_good_alignment_gap",
                    "severity": "high",
                    "guideline_id": "RG-2",
                    "target_id": "",
                    "obligation_unit_id": "",
                    "details": "flag=benchmark_similarity_gap alignment_score=0.55",
                },
                {
                    "deficit_id": "known-good:RG-3:granularity_too_fine",
                    "type": "known_good_alignment_gap",
                    "severity": "medium",
                    "guideline_id": "RG-3",
                    "target_id": "",
                    "obligation_unit_id": "",
                    "details": "flag=granularity_too_fine alignment_score=0.58",
                },
            ]
        }

        candidates = generate_candidates(observation, beam_width=12, max_actions_per_bundle=3)
        action_types = {
            str(action.get("type") or "")
            for candidate in candidates
            for action in candidate.get("actions", [])
        }

        self.assertIn("add_alignment_citation_signals", action_types)
        self.assertIn("raise_benchmark_similarity", action_types)
        self.assertIn("rebalance_alignment_granularity", action_types)

    def test_recommend_handoff_status(self) -> None:
        lane_status = {
            "hard_gate_pass": True,
            "iso_lane_pass": True,
            "decomposition_lane_pass": True,
            "fls_lane_pass": True,
            "quality_lane_pass": True,
        }
        ready = recommend_handoff_status(
            lane_status,
            consecutive_successes=3,
            success_window=3,
            has_run_id=True,
        )
        self.assertEqual(ready[0], "ready")

        needs_review = recommend_handoff_status(
            lane_status,
            consecutive_successes=2,
            success_window=3,
            has_run_id=True,
        )
        self.assertEqual(needs_review[0], "needs_review")

        blocked = recommend_handoff_status(
            {
                **lane_status,
                "fls_lane_pass": False,
            },
            consecutive_successes=5,
            success_window=3,
            has_run_id=True,
        )
        self.assertEqual(blocked[0], "blocked")

    def test_rewrite_rule_statement_specific_removes_placeholder_terms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            guideline_id = "RG-AAAA00000001"
            compliant_code_path = f"tests/guidelines/{guideline_id}/examples/compliant.rs"
            compliant_doc_path = f"tests/guidelines/{guideline_id}/examples/compliant.md"
            non_compliant_code_path = f"tests/guidelines/{guideline_id}/examples/non_compliant.rs"
            non_compliant_doc_path = f"tests/guidelines/{guideline_id}/examples/non_compliant.md"

            write_yaml(
                root / "data" / "todo_guidelines.yaml",
                {
                    "version": 1,
                    "guidelines": [
                        {
                            "id": guideline_id,
                            "category": "Required",
                            "technical_topic": "Language subset / forbidden constructs",
                            "rule_statement": "Placeholder rule",
                            "amplification": (
                                "Initial generic guideline derived from placeholder seed."
                            ),
                            "exceptions": "Pending review",
                            "rationale": "todo rationale",
                            "iso_seeds": ["SEED-A"],
                            "fls_refs": ["fls_unsafety_core"],
                            "scope": "crate",
                            "decidable": "undecidable",
                            "decidability_rationale": "pending",
                            "state": "DRAFT",
                            "enforcement_mode": "AUDIT",
                            "enforcement_details": "details",
                            "evidence_artifacts": [
                                f"tests/guidelines/{guideline_id}/metadata.yaml"
                            ],
                            "deviation_requirements": "dev",
                            "examples": {
                                "compliant": {
                                    "code_path": compliant_code_path,
                                    "doc_path": compliant_doc_path,
                                    "explanation": "ok",
                                    "compile_expectation": "documented-only",
                                },
                                "non_compliant": {
                                    "code_path": non_compliant_code_path,
                                    "doc_path": non_compliant_doc_path,
                                    "explanation": "bad",
                                    "compile_expectation": "documented-only",
                                },
                            },
                        }
                    ],
                },
            )
            compliant_doc = (
                root / "tests" / "guidelines" / guideline_id / "examples" / "compliant.md"
            )
            compliant_doc.parent.mkdir(parents=True, exist_ok=True)
            compliant_doc.write_text("placeholder sample", encoding="utf-8")

            result = apply_rewrite_rule_statement_specific(
                root,
                {
                    "type": "rewrite_rule_statement_specific",
                    "guideline_id": guideline_id,
                },
            )
            self.assertTrue(result["changed"])

            updated = yaml.safe_load(
                (root / "data" / "todo_guidelines.yaml").read_text(encoding="utf-8")
            )
            guideline = updated["guidelines"][0]
            self.assertNotIn("placeholder", guideline["rule_statement"].lower())
            self.assertNotIn("todo", guideline["rationale"].lower())
            self.assertNotIn("pending", guideline["decidability_rationale"].lower())

            rewritten_doc = compliant_doc.read_text(encoding="utf-8").lower()
            self.assertNotIn("placeholder", rewritten_doc)

    def test_spawn_rule_for_obligation_unit_adds_child_guideline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target_id = "ISO26262-6-2018:table_1:clause:table_1"
            obligation = "ISO26262-6-2018:table:table_1:001:1"
            seed_id = "SEED-A"
            parent_id = "RG-PARENT000001"
            parent_compliant_code_path = f"tests/guidelines/{parent_id}/examples/compliant.rs"
            parent_compliant_doc_path = f"tests/guidelines/{parent_id}/examples/compliant.md"
            parent_non_compliant_code_path = (
                f"tests/guidelines/{parent_id}/examples/non_compliant.rs"
            )
            parent_non_compliant_doc_path = (
                f"tests/guidelines/{parent_id}/examples/non_compliant.md"
            )

            write_yaml(
                root / "data" / "seed_topics.yaml",
                {
                    "version": 1,
                    "run_id": "test-run",
                    "seed_topics": [
                        {
                            "seed_id": seed_id,
                            "iso_ref": "Part 6 Table 1",
                            "chunk_id": "chunk-1",
                            "citation": "citation",
                            "topic_phrase": "topic",
                            "context_summary": "summary",
                            "category_candidate": "Language subset / forbidden constructs",
                            "enforceability_hint": "AUTO",
                            "citation_anchor_id": target_id,
                            "obligation_unit_id": obligation,
                        }
                    ],
                },
            )
            write_yaml(
                root / "data" / "todo_guidelines.yaml",
                {
                    "version": 1,
                    "guidelines": [
                        {
                            "id": parent_id,
                            "category": "Required",
                            "technical_topic": "Language subset / forbidden constructs",
                            "rule_statement": "Parent statement",
                            "amplification": "Parent amplification",
                            "exceptions": "Parent exceptions",
                            "rationale": "Parent rationale",
                            "iso_seeds": [seed_id],
                            "fls_refs": ["fls_program_structure_and_compilation_core"],
                            "scope": "crate",
                            "decidable": "decidable",
                            "decidable_status": "possible-with-clippy",
                            "decidability_rationale": "Parent decidability",
                            "state": "DRAFT",
                            "enforcement_mode": "AUTO",
                            "enforcement_details": "details",
                            "evidence_artifacts": [f"tests/guidelines/{parent_id}/metadata.yaml"],
                            "deviation_requirements": "dev",
                            "examples": {
                                "compliant": {
                                    "code_path": parent_compliant_code_path,
                                    "doc_path": parent_compliant_doc_path,
                                    "explanation": "ok",
                                    "compile_expectation": "no_run",
                                },
                                "non_compliant": {
                                    "code_path": parent_non_compliant_code_path,
                                    "doc_path": parent_non_compliant_doc_path,
                                    "explanation": "bad",
                                    "compile_expectation": "compile_pass",
                                },
                            },
                        }
                    ],
                },
            )

            coverage_path = root / "data" / "coverage_matrix.csv"
            coverage_path.parent.mkdir(parents=True, exist_ok=True)
            with coverage_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "target_id",
                        "obligation_unit_id",
                        "seed_id",
                        "guideline_id",
                        "fls_ref",
                        "evidence_path",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "target_id": target_id,
                        "obligation_unit_id": obligation,
                        "seed_id": seed_id,
                        "guideline_id": parent_id,
                        "fls_ref": "fls_program_structure_and_compilation_core",
                        "evidence_path": f"tests/guidelines/{parent_id}/metadata.yaml",
                    }
                )

            result = apply_spawn_rule_for_obligation_unit(
                root,
                {
                    "type": "spawn_rule_for_obligation_unit",
                    "target_id": target_id,
                },
            )
            self.assertTrue(result["changed"])
            self.assertIn("new_guideline_id", result)

            payload = yaml.safe_load(
                (root / "data" / "todo_guidelines.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(len(payload["guidelines"]), 2)
            child = next(
                item for item in payload["guidelines"] if item["id"] == result["new_guideline_id"]
            )
            self.assertEqual(child["decomposition_parent"], parent_id)
            self.assertEqual(child["obligation_units"], [obligation])

    def test_add_alignment_citation_signals_adds_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            guideline_id = "RG-CITATION0001"
            write_yaml(
                root / "data" / "todo_guidelines.yaml",
                {
                    "version": 1,
                    "guidelines": [
                        {
                            "id": guideline_id,
                            "rule_statement": "Use explicit safety constraints.",
                            "amplification": "Describe deterministic enforcement.",
                            "exceptions": "Allow only justified deviations.",
                            "rationale": "Baseline rationale text.",
                            "examples": {},
                        }
                    ],
                },
            )

            result = apply_add_alignment_citation_signals(
                root,
                {
                    "type": "add_alignment_citation_signals",
                    "guideline_id": guideline_id,
                },
            )
            self.assertTrue(result["changed"])

            updated = yaml.safe_load(
                (root / "data" / "todo_guidelines.yaml").read_text(encoding="utf-8")
            )
            rationale = str(updated["guidelines"][0]["rationale"])
            self.assertIn(":cite:`ISO26262-6-2018`", rationale)
            self.assertIn(":std:`std::result::Result`", rationale)

    def test_alignment_overrides_for_iteration_interpolates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_yaml(
                root / "config" / "alignment_policy.yaml",
                {
                    "version": 1,
                    "thresholds": {
                        "min_global_alignment": 0.75,
                        "min_changed_guideline_alignment": 0.8,
                        "granularity_outliers_allowed": 0,
                    },
                    "gate_mode": "warn",
                    "controller_progression": {
                        "enabled": True,
                        "start_iteration": 1,
                        "target_iteration": 5,
                        "start_thresholds": {
                            "min_global_alignment": 0.6,
                            "min_changed_guideline_alignment": 0.65,
                            "granularity_outliers_allowed": 4,
                        },
                        "target_thresholds": {
                            "min_global_alignment": 0.75,
                            "min_changed_guideline_alignment": 0.8,
                            "granularity_outliers_allowed": 0,
                        },
                        "start_gate_mode": "warn",
                        "target_gate_mode": "error",
                    },
                },
            )

            first = alignment_overrides_for_iteration(root, 1)
            middle = alignment_overrides_for_iteration(root, 3)
            final = alignment_overrides_for_iteration(root, 5)

            self.assertEqual(first["min_global_alignment"], 0.6)
            self.assertEqual(first["gate_mode"], "warn")
            self.assertGreater(middle["min_global_alignment"], first["min_global_alignment"])
            self.assertEqual(final["min_global_alignment"], 0.75)
            self.assertEqual(final["min_changed_guideline_alignment"], 0.8)
            self.assertEqual(final["granularity_outliers_allowed"], 0)
            self.assertEqual(final["gate_mode"], "error")


if __name__ == "__main__":
    unittest.main()
