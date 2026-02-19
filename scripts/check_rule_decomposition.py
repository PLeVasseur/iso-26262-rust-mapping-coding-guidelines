#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

from _common import (
    EXIT_POLICY_FAIL,
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    read_yaml,
    repo_root,
    write_json,
)
from _fls_proxy import classify_target_class


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check guideline fanout/decomposition thresholds")
    parser.add_argument("--coverage-matrix", type=Path, default=Path("data/coverage_matrix.csv"))
    parser.add_argument("--policy", type=Path, default=Path("config/completeness_policy.yaml"))
    parser.add_argument("--enforce", action="store_true")
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = repo_root()
    coverage_path = root / args.coverage_matrix
    policy_path = root / args.policy

    if not coverage_path.exists():
        print(f"[decomposition][error] missing coverage matrix: {coverage_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL
    if not policy_path.exists():
        print(f"[decomposition][error] missing policy: {policy_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL

    policy = read_yaml(policy_path) or {}
    target_policy = policy.get("target_fanout_min_by_target_class") or {}
    obligation_min = int(policy.get("obligation_fanout_min_default") or 1)
    gate_mode = str((policy.get("gate_modes") or {}).get("decomposition") or "warn")
    hard_fail = args.enforce or gate_mode == "error"

    with coverage_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    target_guidelines: dict[str, set[str]] = defaultdict(set)
    obligation_guidelines: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        target_id = str(row.get("target_id") or "").strip()
        guideline_id = str(row.get("guideline_id") or "").strip()
        obligation_unit_id = str(row.get("obligation_unit_id") or "").strip()
        if target_id and guideline_id:
            target_guidelines[target_id].add(guideline_id)
        if obligation_unit_id and guideline_id:
            obligation_guidelines[obligation_unit_id].add(guideline_id)

    target_results = []
    obligation_results = []
    errors: list[str] = []
    warnings: list[str] = []

    for target_id in sorted(target_guidelines):
        target_class = classify_target_class(target_id)
        expected_min = int(target_policy.get(target_class, 1))
        actual = len(target_guidelines[target_id])
        ok = actual >= expected_min
        target_results.append(
            {
                "target_id": target_id,
                "target_class": target_class,
                "expected_min": expected_min,
                "actual": actual,
                "ok": ok,
            }
        )
        if not ok:
            message = (
                f"target `{target_id}` ({target_class}) fanout {actual} < required {expected_min}"
            )
            if hard_fail:
                errors.append(message)
            else:
                warnings.append(message)

    for obligation_unit_id in sorted(obligation_guidelines):
        actual = len(obligation_guidelines[obligation_unit_id])
        ok = actual >= obligation_min
        obligation_results.append(
            {
                "obligation_unit_id": obligation_unit_id,
                "expected_min": obligation_min,
                "actual": actual,
                "ok": ok,
            }
        )
        if not ok:
            message = (
                f"obligation `{obligation_unit_id}` fanout {actual} < required {obligation_min}"
            )
            if hard_fail:
                errors.append(message)
            else:
                warnings.append(message)

    report = {
        "target_count": len(target_results),
        "obligation_count": len(obligation_results),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "gate_mode": "error" if hard_fail else "warn",
        "targets": target_results,
        "obligations": obligation_results,
        "errors": errors,
        "warnings": warnings,
        "ok": len(errors) == 0,
    }

    if args.json_output:
        write_json(args.json_output, report)

    if errors:
        print(f"[decomposition] failed with {len(errors)} error(s)")
        for error in errors:
            print(f"[decomposition][error] {error}")
        return EXIT_POLICY_FAIL

    print(
        "[decomposition] "
        f"ok targets={report['target_count']} obligations={report['obligation_count']} "
        f"warnings={report['warning_count']}"
    )
    for warning in warnings:
        print(f"[decomposition][warn] {warning}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
