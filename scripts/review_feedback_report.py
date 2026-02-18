#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import EXIT_POLICY_FAIL, EXIT_SUCCESS, read_yaml, repo_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize diffset review feedback status")
    parser.add_argument("--diffset-id", help="Filter report to one diffset id")
    parser.add_argument("--feedback-dir", type=Path, default=Path("feedback/diffset_reviews"))
    parser.add_argument("--fail-on-blockers", action="store_true")
    parser.add_argument("--fail-on-needs-change", action="store_true")
    return parser.parse_args()


def iter_feedback_files(root: Path, feedback_dir: Path, diffset_id: str | None) -> list[Path]:
    directory = root / feedback_dir
    if not directory.exists():
        return []
    if diffset_id:
        candidate = directory / f"{diffset_id}.yaml"
        return [candidate] if candidate.exists() else []
    return sorted(directory.glob("*.y*ml"))


def main() -> int:
    args = parse_args()
    root = repo_root()
    files = iter_feedback_files(root, args.feedback_dir, args.diffset_id)

    if not files:
        print("[review-report] no diffset review feedback files found")
        return EXIT_SUCCESS

    total_items = 0
    total_block_open = 0
    total_needs_change_open = 0

    for path in files:
        payload = read_yaml(path) or {}
        items = payload.get("items") or []
        block_open = 0
        needs_change_open = 0

        for item in items:
            if not isinstance(item, dict):
                continue
            total_items += 1
            verdict = str(item.get("verdict") or "").strip()
            status = str(item.get("status") or "open").strip()
            if verdict == "block" and status != "resolved":
                block_open += 1
            if verdict == "needs_change" and status != "resolved":
                needs_change_open += 1

        total_block_open += block_open
        total_needs_change_open += needs_change_open
        print(
            "[review-report] "
            f"{path.relative_to(root)} items={len(items)} "
            f"block_open={block_open} needs_change_open={needs_change_open}"
        )

    print(
        "[review-report] totals "
        f"files={len(files)} items={total_items} "
        f"block_open={total_block_open} needs_change_open={total_needs_change_open}"
    )

    if args.fail_on_blockers and total_block_open > 0:
        print("[review-report][error] unresolved blocking review items found")
        return EXIT_POLICY_FAIL
    if args.fail_on_needs_change and total_needs_change_open > 0:
        print("[review-report][error] unresolved needs_change review items found")
        return EXIT_POLICY_FAIL

    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
