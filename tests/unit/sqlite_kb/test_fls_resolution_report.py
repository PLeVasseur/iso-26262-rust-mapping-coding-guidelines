from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.fls_resolution_report import write_resolution_report  # noqa: E402


def test_write_resolution_report_creates_json_artifact(tmp_path: Path) -> None:
    out = write_resolution_report(
        report_root=tmp_path,
        target_id="RET-ISSUE-001",
        title="Unsafe fallback handling",
        payload={"decision": {"reason_code": "ACCEPTED"}},
    )
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["target_id"] == "RET-ISSUE-001"
    assert payload["decision"]["reason_code"] == "ACCEPTED"
