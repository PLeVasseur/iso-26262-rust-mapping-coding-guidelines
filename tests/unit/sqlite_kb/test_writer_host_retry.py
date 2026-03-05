from __future__ import annotations

from retrieval.writer_host import retry as retry_mod


def test_retry_stops_on_same_violations(monkeypatch) -> None:
    monkeypatch.setattr(retry_mod, "_run_opencode_cli", lambda **_: (0, {"ok": False}))

    outcome = retry_mod.run_role_with_retry(
        role_name="evidence_synthesizer",
        prompt="p",
        validate_output=lambda _output: ["same_violation"],
        max_retries=3,
        model=None,
        agent=None,
    )

    assert outcome.oscillation_detected is True
    assert outcome.attempts == 2
    assert "same_violation" in outcome.violations


def test_retry_detects_diminishing_returns(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_run(**_kwargs):
        calls["n"] += 1
        return 0, {"attempt": calls["n"]}

    def validate(output):
        idx = int(output["attempt"])
        if idx == 1:
            return ["a", "b", "c"]
        if idx == 2:
            return ["a", "b"]
        return ["a", "b", "c"]

    monkeypatch.setattr(retry_mod, "_run_opencode_cli", fake_run)
    outcome = retry_mod.run_role_with_retry(
        role_name="evidence_synthesizer",
        prompt="p",
        validate_output=validate,
        max_retries=4,
        model=None,
        agent=None,
    )

    assert outcome.diminishing_returns is True
    assert outcome.attempts == 3


def test_retry_reports_budget_exhausted(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_run(**_kwargs):
        calls["n"] += 1
        return 0, {"attempt": calls["n"]}

    monkeypatch.setattr(retry_mod, "_run_opencode_cli", fake_run)
    outcome = retry_mod.run_role_with_retry(
        role_name="evidence_synthesizer",
        prompt="p",
        validate_output=lambda output: [f"v{output['attempt']}"],
        max_retries=1,
        model=None,
        agent=None,
    )

    assert outcome.budget_exhausted is True
    assert outcome.attempts == 2
