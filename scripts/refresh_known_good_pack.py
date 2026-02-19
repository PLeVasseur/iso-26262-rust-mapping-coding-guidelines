#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from _common import EXIT_RUNTIME_FAIL, EXIT_SUCCESS, repo_root, run_command, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh known-good benchmark pack end-to-end")
    parser.add_argument("--source-sha")
    parser.add_argument("--tier", choices=["strict", "extended", "all"])
    parser.add_argument("--policy", type=Path, default=Path("config/known_good_policy.yaml"))
    parser.add_argument(
        "--alignment-policy",
        type=Path,
        default=Path("config/alignment_policy.yaml"),
    )
    parser.add_argument("--clean", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("benchmarks/known-good/reports/refresh_report.json"),
    )
    return parser.parse_args()


def run_step(root: Path, command: list[str]) -> dict[str, object]:
    completed = run_command([sys.executable, *command], cwd=root)
    return {
        "command": " ".join([sys.executable, *command]),
        "return_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main() -> int:
    args = parse_args()
    root = repo_root()

    if args.clean:
        benchmark_root = root / "benchmarks" / "known-good"
        if benchmark_root.exists():
            shutil.rmtree(benchmark_root)

    harvest_command = [
        "scripts/harvest_known_good_guidelines.py",
        "--policy",
        str(args.policy),
    ]
    if args.source_sha:
        harvest_command.extend(["--source-sha", args.source_sha])
    if args.tier:
        harvest_command.extend(["--tier", args.tier])

    steps = [
        harvest_command,
        ["scripts/translate_known_good_rst_to_md.py"],
        ["scripts/build_known_good_canonical.py"],
        [
            "scripts/build_known_good_feature_baseline.py",
            "--alignment-policy",
            str(args.alignment_policy),
        ],
    ]

    reports = []
    for command in steps:
        report = run_step(root, command)
        reports.append(report)
        if report["return_code"] != 0:
            write_json(root / args.report, {"ok": False, "steps": reports})
            print(f"[known-good-refresh][error] step failed: {' '.join(command)}")
            return EXIT_RUNTIME_FAIL

    write_json(root / args.report, {"ok": True, "steps": reports})
    print("[known-good-refresh] completed")
    print(f"[known-good-refresh] report -> {(root / args.report).relative_to(root)}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
