from __future__ import annotations

from scripts.judges_v2.stage_b import evaluate_judge


def _contracts() -> dict:
    return {
        "roles": {
            "technical_accuracy": {
                "prompt_template_id": "judge-technical-v2",
                "prompt_template_text": "Judge technical accuracy and return JSON.",
                "required_output_schema": {
                    "required": ["decision", "reason_codes", "summary", "details"]
                },
                "forbidden_patterns": ["abstain"],
            }
        }
    }


def test_llm_path_success(monkeypatch) -> None:
    monkeypatch.setattr("scripts.judges_v2.stage_b.create_session", lambda **_: "ses_test")
    monkeypatch.setattr(
        "scripts.judges_v2.stage_b.run_opencode",
        lambda *_args, **_kwargs: (
            0,
            {
                "decision": "pass",
                "reason_codes": ["all_criteria_met"],
                "summary": "Looks good.",
                "details": {"hazard_accurate": True},
            },
        ),
    )

    verdict = evaluate_judge(
        "technical_accuracy",
        ".. guideline:: test\n",
        [],
        _contracts(),
        judge_mode="llm",
        model=None,
    )
    assert verdict["decision"] == "pass"
    assert verdict["prompt_template_id"] == "judge-technical-v2"
    assert verdict["prompt_hash"]
    assert verdict["telemetry"]["transport_exit_code"] == 0


def test_llm_path_missing_required_schema_forces_fail(monkeypatch) -> None:
    monkeypatch.setattr("scripts.judges_v2.stage_b.create_session", lambda **_: "ses_test")
    monkeypatch.setattr(
        "scripts.judges_v2.stage_b.run_opencode",
        lambda *_args, **_kwargs: (0, {"decision": "pass", "summary": "missing fields"}),
    )

    verdict = evaluate_judge(
        "technical_accuracy",
        ".. guideline:: test\n",
        [],
        _contracts(),
        judge_mode="llm",
        model=None,
    )
    assert verdict["decision"] == "fail"
    assert any(str(code).startswith("missing_required_field:") for code in verdict["reason_codes"])


def test_llm_path_forbidden_pattern_forces_fail(monkeypatch) -> None:
    monkeypatch.setattr("scripts.judges_v2.stage_b.create_session", lambda **_: "ses_test")
    monkeypatch.setattr(
        "scripts.judges_v2.stage_b.run_opencode",
        lambda *_args, **_kwargs: (
            0,
            {
                "decision": "pass",
                "reason_codes": [],
                "summary": "abstain should not appear",
                "details": {"hazard_accurate": True},
            },
        ),
    )

    verdict = evaluate_judge(
        "technical_accuracy",
        ".. guideline:: test\n",
        [],
        _contracts(),
        judge_mode="llm",
        model=None,
    )
    assert verdict["decision"] == "fail"
    assert any(str(code).startswith("forbidden_pattern:") for code in verdict["reason_codes"])


def test_llm_path_transport_failure(monkeypatch) -> None:
    monkeypatch.setattr("scripts.judges_v2.stage_b.create_session", lambda **_: "ses_test")
    monkeypatch.setattr(
        "scripts.judges_v2.stage_b.run_opencode", lambda *_args, **_kwargs: (400, None)
    )

    verdict = evaluate_judge(
        "technical_accuracy",
        ".. guideline:: test\n",
        [],
        _contracts(),
        judge_mode="llm",
        model=None,
    )
    assert verdict["decision"] == "fail"
    assert "judge_transport_failure:400" in verdict["reason_codes"]
