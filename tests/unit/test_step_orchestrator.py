from __future__ import annotations

import scripts.step_orchestrator as step_orchestrator

from scripts.step_orchestrator import (
    _extract_prerequisite_paths,
    _get_waiver_token,
    _has_active_waiver,
    _is_optional_prerequisite,
    _normalize_prerequisite_ref,
    _run_semantic_backend_preflight_check,
    _resolve_expected_file_path,
    check_prerequisites,
)


def test_resolve_writer_prompt_contract_basename() -> None:
    path, status, candidates = _resolve_expected_file_path("writer_prompt_contracts.yaml")
    assert status in {"alias", "basename_unique", "exact"}
    assert path is not None
    assert path.name == "writer_prompt_contracts.yaml"
    assert candidates


def test_resolve_rerender_script_basename() -> None:
    path, status, _candidates = _resolve_expected_file_path("rerender_from_artifacts.py")
    assert status in {"basename_unique", "exact"}
    assert path is not None
    assert str(path).endswith("scripts/rendering_v2/rerender_from_artifacts.py")


def test_extract_prerequisite_paths_from_markdown() -> None:
    text = """
## Prerequisites

- [ ] Step 2 complete: `scripts/rendering_v2/rst_renderer.py` exists and tested
- [ ] `{run_dir}/rerendered_rst/` directory exists with re-rendered RST files (from `rerender_from_artifacts.py`)
"""
    paths = _extract_prerequisite_paths(text)
    assert "scripts/rendering_v2/rst_renderer.py" in paths
    assert "rerender_from_artifacts.py" in paths


def test_extract_prerequisite_paths_preserves_dot_cache_paths() -> None:
    text = """
## Prerequisites

- [ ] `.cache/sqlite_kb/reports/phase_a_opencode_v3_exec2/targets.json` exists
"""
    paths = _extract_prerequisite_paths(text)
    assert ".cache/sqlite_kb/reports/phase_a_opencode_v3_exec2/targets.json" in paths
    assert "cache/sqlite_kb/reports/phase_a_opencode_v3_exec2/targets.json" not in paths


def test_resolve_convention_spec_alias() -> None:
    path, status, candidates = _resolve_expected_file_path("convention_spec.json")
    assert status in {"alias", "exact"}
    assert path is not None
    assert str(path).endswith(".cache/convention_spec.json")
    assert candidates


def test_step8_prereq_normalization_and_optional_rules() -> None:
    assert _normalize_prerequisite_ref(8, "__init__.py") == "validation/__init__.py"
    assert _is_optional_prerequisite(8, "retry_pilot_results.json") is True
    assert _is_optional_prerequisite(8, "convention_spec.json") is False


def test_step9_prereq_normalization_and_optional_rules() -> None:
    assert (
        _normalize_prerequisite_ref(
            9, "cache/sqlite_kb/reports/phase_a_opencode_v3_exec2/targets.json"
        )
        == ".cache/sqlite_kb/reports/phase_a_opencode_v3_exec2/targets.json"
    )
    assert _is_optional_prerequisite(9, "scripts/rendering_v2/rst_renderer.py") is True
    assert _is_optional_prerequisite(9, "scripts/validation_v2/conformance.py") is True


def test_run_semantic_backend_preflight_check_reports_success(monkeypatch) -> None:
    class _Result:
        returncode = 0
        stdout = '{"ok": true}'
        stderr = ""

    monkeypatch.setattr(
        "scripts.step_orchestrator.subprocess.run", lambda *args, **kwargs: _Result()
    )

    ok, message = _run_semantic_backend_preflight_check()

    assert ok is True
    assert message


def test_check_prerequisites_step9_fails_when_semantic_backend_unhealthy(monkeypatch) -> None:
    monkeypatch.setitem(step_orchestrator.STEP_DEPS, 9, [])
    monkeypatch.setattr(
        "scripts.step_orchestrator.load_step_file", lambda step_n: "## Prerequisites\n"
    )
    monkeypatch.setattr(
        "scripts.step_orchestrator._run_semantic_backend_preflight_check",
        lambda: (False, "backend unavailable"),
    )

    ok, failures, details = check_prerequisites(9)

    assert ok is False
    assert any("semantic backend unhealthy" in item for item in failures)
    assert any(
        detail.get("kind") == "check" and detail.get("status") == "fail" for detail in details
    )


def test_check_prerequisites_step9_marks_optional_paths(monkeypatch) -> None:
    monkeypatch.setitem(step_orchestrator.STEP_DEPS, 9, [])
    monkeypatch.setattr(
        "scripts.step_orchestrator.load_step_file",
        lambda step_n: "## Prerequisites\n\n- [ ] `scripts/rendering_v2/rst_renderer.py`\n",
    )
    monkeypatch.setattr(
        "scripts.step_orchestrator._run_semantic_backend_preflight_check",
        lambda: (True, "ok"),
    )
    monkeypatch.setattr(
        "scripts.step_orchestrator._resolve_expected_file_path",
        lambda path_ref: (None, "missing", []),
    )

    ok, failures, details = check_prerequisites(9)

    assert ok is True
    assert failures == []
    assert any(
        detail.get("kind") == "file"
        and detail.get("ref") == "scripts/rendering_v2/rst_renderer.py"
        and detail.get("status") == "optional"
        for detail in details
    )


def test_step8_bibliography_waiver_rule_active() -> None:
    token = _get_waiver_token(8, "rendering/bibliography.py")
    assert token == "STEP7-BIB-PATH"
    assert _has_active_waiver(token, "rendering/bibliography.py") is True
