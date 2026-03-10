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


def test_run_validation_is_ws7_compatibility_wrapper(monkeypatch) -> None:
    monkeypatch.setattr(
        validate_fls_matching.validate_fls_ws7,
        "run_validation",
        lambda **kwargs: {
            "runtime_mode": "ws7_staged_retrieval_v1",
            "item_count": 1,
            "proof_valid": True,
        },
    )

    report = validate_fls_matching.run_validation(sweep=False)

    assert report["runtime_mode"] == "ws7_staged_retrieval_v1"
    assert report["compatibility_wrapper"] is True
    assert report["canonical_script"] == "validate_fls_ws7.py"


def test_run_validation_rejects_ws7_sweep() -> None:
    with pytest.raises(RuntimeError, match="not implemented for ws7_staged_retrieval_v1"):
        validate_fls_matching.run_validation(sweep=True)


def test_write_validation_report_prefers_run_dir(tmp_path: Path) -> None:
    out = validate_fls_matching.write_validation_report(
        {"runtime_mode": "ws7_staged_retrieval_v1"},
        run_dir=tmp_path,
    )

    assert out == tmp_path / "ws7_validation.json"
    assert out.exists()


def test_write_validation_report_allows_explicit_output_path(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "report.json"

    out = validate_fls_matching.write_validation_report(
        {"runtime_mode": "ws7_staged_retrieval_v1"},
        run_dir=tmp_path,
        output_path=output,
    )

    assert out == output
    assert (
        json.loads(output.read_text(encoding="utf-8"))["runtime_mode"] == "ws7_staged_retrieval_v1"
    )
