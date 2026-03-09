from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_fls_matching  # noqa: E402


def test_run_validation_includes_baseline(monkeypatch) -> None:
    monkeypatch.setattr(
        validate_fls_matching, "load_calibration_items", lambda **kwargs: [{"path": "x"}]
    )
    monkeypatch.setattr(
        validate_fls_matching,
        "evaluate_calibration_items",
        lambda **kwargs: {
            "total": 1,
            "ws7_required": 1,
            "abstention_correct": 1,
            "structurally_valid": 1,
        },
    )

    report = validate_fls_matching.run_validation(sweep=False)

    assert report["item_count"] == 1
    assert report["baseline"]["total"] == 1
    assert report["runtime_mode"] == "grounding_only_ws6"
    assert report["non_authoritative_for_ws7"] is True


def test_run_validation_rejects_sweep_until_ws7() -> None:
    with pytest.raises(RuntimeError, match="WS7_REQUIRED"):
        validate_fls_matching.run_validation(sweep=True)


def test_run_validation_rejects_runtime_use_prohibited_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "heldout.json"
    dataset.write_text(
        json.dumps({"runtime_use_prohibited": True, "items": []}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="runtime_use_prohibited"):
        validate_fls_matching.run_validation(dataset_path=dataset)


def test_write_validation_report_prefers_run_dir(tmp_path: Path) -> None:
    out = validate_fls_matching.write_validation_report(
        {"runtime_mode": "grounding_only_ws6"},
        run_dir=tmp_path,
    )

    assert out == tmp_path / "fls_grounding_runtime_validation.json"
    assert out.exists()


def test_write_validation_report_allows_explicit_output_path(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "report.json"

    out = validate_fls_matching.write_validation_report(
        {"runtime_mode": "grounding_only_ws6"},
        run_dir=tmp_path,
        output_path=output,
    )

    assert out == output
    assert json.loads(output.read_text(encoding="utf-8"))["runtime_mode"] == "grounding_only_ws6"
