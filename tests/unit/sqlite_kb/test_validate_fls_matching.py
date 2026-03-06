from __future__ import annotations

import sys
from pathlib import Path

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
        lambda **kwargs: {"total": 1, "strict_top1": 1, "topk_ratio": 1.0, "unresolved": 0},
    )
    monkeypatch.setattr(validate_fls_matching, "_effective_policy", lambda _overrides=None: {})

    report = validate_fls_matching.run_validation(sweep=False)

    assert report["item_count"] == 1
    assert report["baseline"]["total"] == 1


def test_run_validation_includes_sweep_when_requested(monkeypatch) -> None:
    monkeypatch.setattr(
        validate_fls_matching, "load_calibration_items", lambda **kwargs: [{"path": "x"}]
    )
    monkeypatch.setattr(
        validate_fls_matching,
        "evaluate_calibration_items",
        lambda **kwargs: {"total": 1, "strict_top1": 1, "topk_ratio": 1.0, "unresolved": 0},
    )
    monkeypatch.setattr(validate_fls_matching, "_effective_policy", lambda _overrides=None: {})
    monkeypatch.setattr(
        validate_fls_matching,
        "run_threshold_sweep",
        lambda **kwargs: {"candidate_count": 1, "best": {"thresholds": {}}},
    )

    report = validate_fls_matching.run_validation(sweep=True)

    assert "sweep" in report
    assert report["sweep"]["candidate_count"] == 1
