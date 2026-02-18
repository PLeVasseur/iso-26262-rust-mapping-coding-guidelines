#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys

from _common import (
    EXIT_RUNTIME_FAIL,
    find_registry_baseline,
    read_yaml,
    repo_root,
    run_command,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap a deterministic session")
    parser.add_argument("--mode", choices=["change", "growth"], default="change")
    parser.add_argument("--profile", choices=["quick", "full"], default="quick")
    parser.add_argument("--corpus-pack", help="Override default corpus pack")
    parser.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="Require an existing baseline run from data/run_registry.yaml",
    )
    return parser.parse_args()


def run_and_stream(command: list[str], cwd, label: str) -> int:
    result = run_command(command, cwd=cwd)
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        print(f"[bootstrap][error] {label} failed: {' '.join(command)}")
    return result.returncode


def main() -> int:
    args = parse_args()
    root = repo_root()

    if not shutil.which("uv"):
        print("[bootstrap][error] uv not found")
        return EXIT_RUNTIME_FAIL

    if run_and_stream(["uv", "sync", "--frozen"], cwd=root, label="uv sync") != 0:
        return EXIT_RUNTIME_FAIL

    registry = read_yaml(root / "config" / "corpus_registry.yaml")
    run_registry = read_yaml(root / "data" / "run_registry.yaml")
    corpus_pack = args.corpus_pack or registry["default_corpus_pack"]

    baseline_entry = find_registry_baseline(run_registry, corpus_pack, args.mode)
    orchestrate_command = [
        "uv",
        "run",
        "python",
        "scripts/orchestrate.py",
        "--mode",
        args.mode,
        "--profile",
        args.profile,
        "--corpus-pack",
        corpus_pack,
        "--allow-missing-traceability",
    ]

    if baseline_entry:
        orchestrate_command.extend(["--base-run", baseline_entry["accepted_run_id"]])
    elif not args.no_bootstrap:
        orchestrate_command.append("--allow-bootstrap")

    code = run_and_stream(orchestrate_command, cwd=root, label="orchestrate")
    if code != 0:
        return code

    print("[bootstrap] session bootstrap completed")
    if baseline_entry:
        print(f"[bootstrap] compared against baseline run: {baseline_entry['accepted_run_id']}")
    else:
        print("[bootstrap] no baseline run found; run executed in bootstrap mode")
    return 0


if __name__ == "__main__":
    sys.exit(main())
