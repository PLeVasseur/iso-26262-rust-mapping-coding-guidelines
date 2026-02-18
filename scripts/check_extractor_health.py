#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import (
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    ensure_extractor_paths,
    resolve_extractor_paths,
    run_command,
    utc_now,
    write_json,
)


def run_probe(command: list[str], cwd: Path) -> dict[str, object]:
    result = run_command(command, cwd=cwd)
    return {
        "command": " ".join(command),
        "return_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "ok": result.returncode == 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify extractor health and readiness")
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip `iso26262 validate` during preflight",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Optional path to write machine-readable report",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = resolve_extractor_paths()
    issues = ensure_extractor_paths(paths)

    report: dict[str, object] = {
        "generated_at": utc_now(),
        "paths": {
            "repo_root": str(paths.repo_root),
            "cache_root": str(paths.cache_root),
            "manifest_dir": str(paths.manifest_dir),
        },
        "path_issues": issues,
        "checks": [],
        "ok": False,
    }

    if issues:
        if args.json_output:
            write_json(args.json_output, report)
        for issue in issues:
            print(f"[health][error] {issue}")
        return EXIT_RUNTIME_FAIL

    checks: list[dict[str, object]] = []
    checks.append(
        run_probe(
            [
                "cargo",
                "run",
                "--quiet",
                "--",
                "status",
                "--cache-root",
                str(paths.cache_root),
            ],
            cwd=paths.repo_root,
        )
    )

    if not args.skip_validate:
        checks.append(
            run_probe(
                [
                    "cargo",
                    "run",
                    "--quiet",
                    "--",
                    "validate",
                    "--cache-root",
                    str(paths.cache_root),
                ],
                cwd=paths.repo_root,
            )
        )

    report["checks"] = checks
    report["ok"] = all(bool(check.get("ok")) for check in checks)

    if args.json_output:
        write_json(args.json_output, report)

    if report["ok"]:
        print("[health] extractor preflight passed")
        return EXIT_SUCCESS

    print("[health] extractor preflight failed")
    for check in checks:
        if not check["ok"]:
            print(f"[health][failed] {check['command']}")
    return EXIT_RUNTIME_FAIL


if __name__ == "__main__":
    sys.exit(main())
