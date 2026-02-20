#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlite_build_rust_reference import validate_rust_reference_db

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate rust_reference.sqlite and emit report")
    parser.add_argument(
        "--db-path",
        default=".cache/sqlite_kb/current/rust_reference.sqlite",
        help="Path to active rust_reference sqlite database",
    )
    parser.add_argument(
        "--previous-snapshot-path",
        default=None,
        help="Optional previous snapshot path for drift checks",
    )
    parser.add_argument(
        "--report-path",
        default=None,
        help="Optional report output file path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    db_path = (root / args.db_path).resolve()
    previous_snapshot_path = (
        Path(args.previous_snapshot_path).expanduser().resolve()
        if args.previous_snapshot_path
        else None
    )

    try:
        report = validate_rust_reference_db(
            db_path=db_path,
            previous_snapshot_path=previous_snapshot_path,
        )
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"[validate-rust-reference][error] {exc}")
        return EXIT_RUNTIME_FAIL

    if args.report_path:
        report_path = (root / args.report_path).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")

    print(json.dumps(report, indent=2, sort_keys=True))
    return EXIT_SUCCESS if report.get("passed") else EXIT_RUNTIME_FAIL


if __name__ == "__main__":
    sys.exit(main())
