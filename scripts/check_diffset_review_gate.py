#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import EXIT_POLICY_FAIL, EXIT_SUCCESS, read_yaml, repo_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enforce diffset review blocker policy")
    parser.add_argument("--diffset-id", help="Specific diffset id to evaluate")
    parser.add_argument("--feedback-dir", type=Path, default=Path("feedback/diffset_reviews"))
    parser.add_argument("--allow-missing", action="store_true")
    return parser.parse_args()


def resolve_feedback_file(root: Path, feedback_dir: Path, diffset_id: str | None) -> Path | None:
    directory = root / feedback_dir
    if not directory.exists():
        return None

    if diffset_id:
        candidate = directory / f"{diffset_id}.yaml"
        return candidate if candidate.exists() else None

    files = sorted(directory.glob("*.y*ml"), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def main() -> int:
    args = parse_args()
    root = repo_root()

    feedback_file = resolve_feedback_file(root, args.feedback_dir, args.diffset_id)
    if feedback_file is None:
        message = "no diffset review feedback file found"
        if args.allow_missing:
            print(f"[review-gate] {message}; passing because --allow-missing")
            return EXIT_SUCCESS
        print(f"[review-gate][error] {message}")
        return EXIT_POLICY_FAIL

    payload = read_yaml(feedback_file) or {}
    items = payload.get("items") or []

    unresolved_blocks: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        verdict = str(item.get("verdict") or "").strip()
        status = str(item.get("status") or "open").strip()
        if verdict == "block" and status != "resolved":
            unresolved_blocks.append(str(item.get("item_id") or "unknown-item"))

    if unresolved_blocks:
        print(
            "[review-gate][error] unresolved blocking review items: "
            f"{', '.join(unresolved_blocks[:20])}"
        )
        return EXIT_POLICY_FAIL

    print(f"[review-gate] passed ({feedback_file.relative_to(root)}) unresolved_block_count=0")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
