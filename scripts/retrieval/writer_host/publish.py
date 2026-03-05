from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from retrieval.services import guidelines_repo_service


def run_publish(*, root: Path, mode: str, profile: str, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {
            "status": "dry_run",
            "mode": mode,
            "profile": profile,
            "operation": "guidelines_repo.autopilot",
        }

    args = Namespace(
        command_family="guidelines-repo",
        guidelines_subcommand="autopilot",
        mode=mode,
        profile=profile,
        allow_main=(mode == "exploratory"),
    )
    code = guidelines_repo_service.run_autopilot(args, root=root)
    return {
        "status": "pass" if int(code) == 0 else "fail",
        "mode": mode,
        "profile": profile,
        "returncode": int(code),
    }


def write_publish_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")
