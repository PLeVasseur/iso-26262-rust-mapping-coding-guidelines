#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from _common import (
    EXIT_POLICY_FAIL,
    EXIT_SUCCESS,
    read_yaml,
    repo_root,
    write_json,
)

REQUIRED_COLUMNS = ["target_id", "seed_id", "guideline_id", "evidence_path"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check traceability matrix completeness")
    parser.add_argument("--coverage-matrix", type=Path, default=Path("data/coverage_matrix.csv"))
    parser.add_argument("--todo-guidelines", type=Path, default=Path("data/todo_guidelines.yaml"))
    parser.add_argument("--target-scope", type=Path, default=Path("data/target_scope.yaml"))
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = repo_root()
    coverage_path = root / args.coverage_matrix
    guidelines_path = root / args.todo_guidelines
    scope_path = root / args.target_scope

    errors: list[str] = []
    warnings: list[str] = []
    rows: list[dict[str, str]] = []

    if not coverage_path.exists():
        message = f"coverage matrix missing: {coverage_path.relative_to(root)}"
        if args.allow_missing:
            warnings.append(message)
        else:
            errors.append(message)
    else:
        with coverage_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                errors.append("coverage matrix has no header row")
            else:
                for column in REQUIRED_COLUMNS:
                    if column not in reader.fieldnames:
                        errors.append(f"coverage matrix missing required column: {column}")

            rows = list(reader)
            for index, row in enumerate(rows, start=2):
                for column in REQUIRED_COLUMNS:
                    value = (row.get(column) or "").strip()
                    if not value:
                        errors.append(f"row {index} has empty `{column}`")

    guideline_ids: set[str] = set()
    if guidelines_path.exists():
        guidelines_payload = read_yaml(guidelines_path) or {}
        guideline_ids = {
            str(item.get("id", "")).strip()
            for item in guidelines_payload.get("guidelines", [])
            if str(item.get("id", "")).strip()
        }
    elif not args.allow_missing:
        errors.append(f"todo guidelines file missing: {guidelines_path.relative_to(root)}")

    coverage_guideline_ids = {row.get("guideline_id", "").strip() for row in rows if row}
    for guideline_id in sorted(guideline_ids):
        if guideline_id not in coverage_guideline_ids:
            errors.append(f"guideline missing coverage row: {guideline_id}")

    if scope_path.exists():
        scope = read_yaml(scope_path) or {}
        in_scope = {
            str(value).strip()
            for value in scope.get("in_scope_target_ids", [])
            if str(value).strip()
        }
        coverage_targets = {row.get("target_id", "").strip() for row in rows if row}
        missing_targets = sorted(in_scope - coverage_targets)
        for target_id in missing_targets:
            errors.append(f"in-scope target missing from coverage matrix: {target_id}")

    report = {
        "coverage_row_count": len(rows),
        "guideline_count": len(guideline_ids),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "ok": len(errors) == 0,
    }

    if args.json_output:
        write_json(args.json_output, report)

    if report["ok"]:
        print(f"[traceability] rows={report['coverage_row_count']}")
        print(f"[traceability] guidelines={report['guideline_count']}")
        if warnings:
            for warning in warnings:
                print(f"[traceability][warn] {warning}")
        return EXIT_SUCCESS

    print(f"[traceability] failed with {len(errors)} error(s)")
    for error in errors:
        print(f"[traceability][error] {error}")
    return EXIT_POLICY_FAIL


if __name__ == "__main__":
    sys.exit(main())
