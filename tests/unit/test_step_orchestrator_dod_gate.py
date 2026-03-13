from __future__ import annotations

from pathlib import Path

from scripts import step_orchestrator as orchestrator


def _stub_run_log(tmp_path: Path) -> Path:
    path = tmp_path / "step_run.json"
    path.write_text("{}", encoding="utf-8")
    return path


def test_execute_step_halts_on_dod_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(orchestrator, "check_prerequisites", lambda step_n: (True, [], []))
    monkeypatch.setattr(orchestrator, "compose_prompt", lambda step_n: "prompt")
    monkeypatch.setattr(
        orchestrator,
        "run_opencode_session",
        lambda step_n, prompt: (0, "STEP_8_COMPLETE", "", "session", _stub_run_log(tmp_path)),
    )
    monkeypatch.setattr(
        orchestrator,
        "validate_dod",
        lambda step_n: {"file_exists:validation/role_validators.py": True, "tests_pass": False},
    )

    def _unexpected_invariants() -> bool:
        raise AssertionError("invariants should not run on DoD failure")

    def _unexpected_regression() -> tuple[bool, str]:
        raise AssertionError("regression smoke should not run on DoD failure")

    monkeypatch.setattr(orchestrator, "run_invariants", _unexpected_invariants)
    monkeypatch.setattr(orchestrator, "run_regression_smoke", _unexpected_regression)

    result = orchestrator.execute_step(8)

    assert result.status == "halt"
    assert "DOD_FAILED" in result.halt_reason
    assert result.dod_failures == ["tests_pass"]
    assert result.dod_override_used is False
    assert result.invariants_passed is None
    assert result.regression_passed is None


def test_execute_step_allows_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ORCHESTRATOR_ALLOW_DOD_FAILURE", "1")
    monkeypatch.setattr(orchestrator, "check_prerequisites", lambda step_n: (True, [], []))
    monkeypatch.setattr(orchestrator, "compose_prompt", lambda step_n: "prompt")
    monkeypatch.setattr(
        orchestrator,
        "run_opencode_session",
        lambda step_n, prompt: (0, "STEP_8_COMPLETE", "", "session", _stub_run_log(tmp_path)),
    )
    monkeypatch.setattr(orchestrator, "validate_dod", lambda step_n: {"tests_pass": False})
    monkeypatch.setattr(orchestrator, "run_invariants", lambda: True)
    monkeypatch.setattr(orchestrator, "run_regression_smoke", lambda: (True, "ok"))

    result = orchestrator.execute_step(8)

    assert result.status != "halt"
    assert result.dod_failures == ["tests_pass"]
    assert result.dod_override_used is True
    assert result.invariants_passed is True
    assert result.regression_passed is True


def test_generate_step_review_renders_dod_gate_warning(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(orchestrator, "REVIEWS_DIR", tmp_path)
    result = orchestrator.StepResult(
        step=8,
        status="pass",
        duration_s=1.0,
        dod_checks={"tests_pass": False},
        dod_failures=["tests_pass"],
        dod_override_used=True,
        invariants_passed=True,
        regression_passed=True,
    )

    review_path = orchestrator.generate_step_review(8, result)
    text = review_path.read_text(encoding="utf-8")

    assert "[WARN] DoD Gate: override active; failed checks: tests_pass" in text
