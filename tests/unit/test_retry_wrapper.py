from __future__ import annotations

from scripts.opencode_retry_wrapper import is_likely_truncated, retry_with_violations


def test_budget_enforcement() -> None:
    calls = 0

    def parse_violations(_output):
        return ["violation_a"]

    def build_retry_prompt(_initial, violations):
        return f"retry: {violations}"

    import scripts.opencode_retry_wrapper as wrapper

    original = wrapper.run_opencode

    def fake_run(_session_id, _prompt, **_kwargs):
        nonlocal calls
        calls += 1
        return 0, {"role_output": "content"}

    wrapper.run_opencode = fake_run
    try:
        result = retry_with_violations(
            "test-session",
            "initial",
            parse_violations,
            build_retry_prompt,
            budget=3,
        )
        assert result.budget_exhausted
        assert result.attempts == 3
        assert calls == 3
    finally:
        wrapper.run_opencode = original


def test_truncation_detection() -> None:
    assert is_likely_truncated({"a": None, "b": "", "c": []})

    assert is_likely_truncated(
        {"a": "value", "b": None, "c": None, "d": None, "e": None},
        expected_field_count=10,
    )

    assert not is_likely_truncated(
        {"a": "value", "b": "value", "c": "value", "d": "value"},
        expected_field_count=10,
    )


def test_oscillation_detection_excludes_reappearing_violation() -> None:
    outputs = [
        {"violations": ["a"]},
        {"violations": ["b"]},
        {"violations": ["a"]},
        {"violations": ["a"]},
    ]
    idx = 0

    def parse_violations(output):
        return list(output.get("violations", []))

    def build_retry_prompt(_initial, violations):
        return f"retry: {violations}"

    import scripts.opencode_retry_wrapper as wrapper

    original = wrapper.run_opencode

    def fake_run(_session_id, _prompt, **_kwargs):
        nonlocal idx
        current = outputs[min(idx, len(outputs) - 1)]
        idx += 1
        return 0, current

    wrapper.run_opencode = fake_run
    try:
        result = retry_with_violations(
            "test-session",
            "initial",
            parse_violations,
            build_retry_prompt,
            budget=6,
        )
        assert result.success
        assert result.oscillation_detected
        assert "a" in result.violations_remaining
    finally:
        wrapper.run_opencode = original
