from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_fls_ws7_legacy_paths  # noqa: E402


def test_run_audit_reports_retired_helpers() -> None:
    report = validate_fls_ws7_legacy_paths.run_audit()

    assert report["status"] == "pass"
    assert all(bool(value) for value in report["checks"].values())


def test_write_report_uses_explicit_output(tmp_path: Path) -> None:
    out = validate_fls_ws7_legacy_paths.write_report(
        {"status": "pass", "checks": {}}, output_path=tmp_path / "audit.json"
    )

    assert out == tmp_path / "audit.json"
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "pass"
