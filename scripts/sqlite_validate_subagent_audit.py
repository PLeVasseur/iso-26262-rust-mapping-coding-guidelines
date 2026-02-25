#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from retrieval.eval.audit_contract import validate_audit_markdown

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate subagent audit markdown contract (JSON+markdown)"
    )
    parser.add_argument("--audit-report-path", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    report_path = Path(str(args.audit_report_path).strip())
    if not report_path.is_absolute():
        report_path = (root / report_path).resolve()

    if not report_path.exists():
        print(f"[sqlite_validate_subagent_audit][error] report not found: {report_path}")
        return EXIT_RUNTIME_FAIL

    errors = validate_audit_markdown(report_path)
    if errors:
        print("[sqlite_validate_subagent_audit][error] audit validation failed")
        for err in errors:
            print(f"- {err}")
        return EXIT_RUNTIME_FAIL

    print(f"[sqlite_validate_subagent_audit] pass -> {report_path}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
