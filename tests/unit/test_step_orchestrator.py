from __future__ import annotations

from scripts.step_orchestrator import (
    _extract_prerequisite_paths,
    _get_waiver_token,
    _has_active_waiver,
    _is_optional_prerequisite,
    _normalize_prerequisite_ref,
    _resolve_expected_file_path,
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


def test_step8_bibliography_waiver_rule_active() -> None:
    token = _get_waiver_token(8, "rendering/bibliography.py")
    assert token == "STEP7-BIB-PATH"
    assert _has_active_waiver(token, "rendering/bibliography.py") is True
