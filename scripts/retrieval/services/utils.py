from __future__ import annotations

import json
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(UTC).strftime('%Y%m%d')}"


def _run_id(args: Namespace) -> str:
    value = str(getattr(args, "run_id", "") or "").strip()
    return value or _now_id("s0_phase_a")


def _report_dir(root: Path, run_id: str, report_root: str = "") -> Path:
    if report_root:
        target = Path(report_root)
        if not target.is_absolute():
            target = (root / target).resolve()
        return target
    return (root / ".cache" / "sqlite_kb" / "reports" / run_id).resolve()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows
