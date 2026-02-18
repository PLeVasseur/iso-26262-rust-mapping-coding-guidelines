#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from _common import EXIT_POLICY_FAIL, EXIT_SUCCESS, repo_root, write_json

FORBIDDEN_KEYS = {
    "text",
    "snippet",
    "chunk_text",
    "table_md",
    "table_csv",
    "raw_text",
    "content",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Guard against storing ISO verbatim text")
    parser.add_argument(
        "--paths",
        nargs="*",
        default=["config", "data", "seeds", "feedback", "docs"],
        help="Relative paths to scan",
    )
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def parse_structured_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        if path.suffix in {".yaml", ".yml"}:
            return yaml.safe_load(handle)
        return json.load(handle)


def walk_forbidden_keys(value: Any, source: str, issues: list[str], key_path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in FORBIDDEN_KEYS:
                issues.append(f"{source}: forbidden key `{key}` at {key_path}")
            walk_forbidden_keys(child, source, issues, f"{key_path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            walk_forbidden_keys(child, source, issues, f"{key_path}[{index}]")


def iter_candidate_files(root: Path, relative_paths: list[str]) -> list[Path]:
    candidates: list[Path] = []
    for rel in relative_paths:
        base = root / rel
        if not base.exists():
            continue
        if base.is_file():
            candidates.append(base)
            continue
        candidates.extend(
            file
            for file in base.rglob("*")
            if file.is_file() and file.suffix in {".yaml", ".yml", ".json"}
        )
    return sorted(candidates)


def main() -> int:
    args = parse_args()
    root = repo_root()
    files = iter_candidate_files(root, args.paths)

    issues: list[str] = []
    scanned = 0
    for file in files:
        scanned += 1
        try:
            payload = parse_structured_file(file)
        except Exception as exc:
            issues.append(f"{file.relative_to(root)}: failed to parse structured file ({exc})")
            continue

        walk_forbidden_keys(payload, str(file.relative_to(root)), issues)

    report = {
        "scanned_file_count": scanned,
        "issue_count": len(issues),
        "issues": issues,
        "ok": len(issues) == 0,
    }

    if args.json_output:
        write_json(args.json_output, report)

    if report["ok"]:
        print(f"[licensing-guard] scanned={scanned} no forbidden keys found")
        return EXIT_SUCCESS

    print(f"[licensing-guard] found {len(issues)} issue(s)")
    for issue in issues:
        print(f"[licensing-guard][error] {issue}")
    return EXIT_POLICY_FAIL


if __name__ == "__main__":
    sys.exit(main())
