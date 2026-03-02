from __future__ import annotations

from scripts.step_orchestrator import _extract_prerequisite_paths, _resolve_expected_file_path


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
