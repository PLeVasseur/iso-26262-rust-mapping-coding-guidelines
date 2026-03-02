from __future__ import annotations

import json
from pathlib import Path

from scripts.judges_v2.run_judges import run_judges
from scripts.judges_v2.stage_b import (
    _build_judge_prompt,
    _compute_verdict,
    _normalize_judge_decision,
)
from scripts.validation_v2.run_scope_check import run_scope_check
from validation.scope import check_scope_cardinality


def test_scope_cardinality_single_and_two_family_pass() -> None:
    single_passed, single = check_scope_cardinality(
        ["AtomicBool", "Ordering", "fence"],
        "CORE-CONC-003",
    )
    two_passed, two = check_scope_cardinality(["AtomicBool", "Arc"], "CORE-CONC-003")
    assert single_passed
    assert single["family_count"] == 1
    assert two_passed
    assert two["family_count"] == 2


def test_scope_cardinality_three_family_fails_with_splits() -> None:
    passed, result = check_scope_cardinality(["AtomicBool", "Mutex", "Pin"], "X")
    assert not passed
    assert result["family_count"] == 3
    assert "suggested_splits" in result
    families = {row["family"] for row in result["suggested_splits"]}
    assert families == {"atomics", "concurrency_sync", "pinning"}


def test_scope_unknown_empty_case_and_path_normalization() -> None:
    passed_unknown, result_unknown = check_scope_cardinality(["AtomicBool", "my_custom_type"], "X")
    passed_empty, result_empty = check_scope_cardinality([], "X")
    passed_path, result_path = check_scope_cardinality(["std::sync::atomic::AtomicBool"], "X")
    assert passed_unknown
    assert result_unknown["family_count"] == 1
    assert passed_empty
    assert result_empty["family_count"] == 0
    assert passed_path
    assert result_path["family_count"] == 1


def test_scope_case_insensitivity() -> None:
    passed, result = check_scope_cardinality(["atomicbool", "ORDERING"], "X")
    assert passed
    assert result["family_count"] == 1


def test_scope_per_prompt_override() -> None:
    cfg = {"max_families": 2, "per_prompt_overrides": {"RET-ISSUE-005": 3}}
    passed, result = check_scope_cardinality(
        ["AtomicBool", "Mutex", "Pin"],
        "RET-ISSUE-005",
        config=cfg,
    )
    assert passed
    assert result["max_allowed"] == 3


def test_judge_decision_normalization_and_verdict_logic() -> None:
    reasons: list[str] = []
    assert _normalize_judge_decision("abstain", reasons) == "fail"
    assert "judge_abstained_treated_as_fail" in reasons

    reasons = []
    assert _normalize_judge_decision("maybe", reasons) == "fail"
    assert "unexpected_decision_value:maybe" in reasons

    assert (
        _compute_verdict(
            {
                "technical_accuracy": "pass",
                "functional_safety_relevance": "pass",
                "pedagogical_quality": "pass",
            }
        )
        == "candidate"
    )
    assert (
        _compute_verdict(
            {
                "technical_accuracy": "pass",
                "functional_safety_relevance": "fail",
                "pedagogical_quality": "pass",
            }
        )
        == "blocked"
    )


def test_build_judge_prompt_uses_rst_not_draft_json() -> None:
    prompt = _build_judge_prompt(
        "technical_accuracy",
        {
            "prompt_template_text": "Judge this.",
            "required_output_schema": {"required": ["decision"]},
            "forbidden_patterns": ["abstain"],
        },
        ".. rust-example::\n   :mode: noncompliant\n\n   let _x = 1;",
        ["AtomicBool"],
    )
    assert ".. rust-example::" in prompt
    assert '"construct_terms"' not in prompt


def test_scope_runner_and_judges_runner_integration(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "writer_subagent_outputs").mkdir(parents=True)
    (run_dir / "rerendered_rst").mkdir(parents=True)

    drafts = [
        {
            "draft_id": "d1",
            "target_id": "t1",
            "target_prompt_id": "CORE-CONC-003",
            "status": "drafted",
            "construct_terms": ["AtomicBool", "Ordering"],
        }
    ]
    (run_dir / "drafts.jsonl").write_text(
        "\n".join(json.dumps(row) for row in drafts) + "\n",
        encoding="utf-8",
    )
    (run_dir / "writer_subagent_outputs" / "evidence_synthesizer.jsonl").write_text(
        json.dumps({"draft_id": "d1", "target_id": "t1", "construct_terms": ["AtomicBool"]}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "rerendered_rst" / "core-conc-003.rst").write_text(
        """.. guideline:: Use AtomicBool safely
   :id: gui_AaBbCcDdEeF1

   This rule shall avoid hazards.

   .. rationale::
      because explicit ordering matters

   .. non_compliant_example::
      .. rust-example::
         let _x = 1;

   .. compliant_example::
      .. rust-example::
         let _y = 2;

   .. bibliography::
""",
        encoding="utf-8",
    )

    run_scope_check(run_dir, policy_path=Path("config/s0/scope_gate_policy.yaml"))
    report = run_judges(
        run_dir=run_dir,
        contracts_path=Path("config/s0/judge_prompt_contracts.yaml"),
        scope_report_path=run_dir / "scope_cardinality_report.json",
        judge_mode="heuristic",
    )
    assert report["review_count"] == 0
    assert report["candidate_grade_count"] == 1
    assert report["verdict_triage_applied"] is True
