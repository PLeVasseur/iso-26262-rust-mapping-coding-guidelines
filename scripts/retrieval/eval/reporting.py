from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def infer_root_cause_run_and_cell(report_path: Path) -> tuple[str, str]:
    parts = list(report_path.parts)
    run_id = ""
    cell_id = ""

    if "root_cause" in parts:
        idx = parts.index("root_cause")
        if idx + 1 < len(parts):
            run_id = str(parts[idx + 1]).strip()

    if "matrix" in parts:
        idx = parts.index("matrix")
        if idx + 1 < len(parts):
            cell_id = str(parts[idx + 1]).strip()

    return run_id, cell_id


def write_eval_report(report_path: Path, report: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
