#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import EXIT_RUNTIME_FAIL, EXIT_SUCCESS, read_yaml, repo_root, utc_now, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Carry review feedback forward to a new diffset")
    parser.add_argument("--previous-diffset-id", required=True)
    parser.add_argument("--current-diffset-id", required=True)
    parser.add_argument("--feedback-dir", type=Path, default=Path("feedback/diffset_reviews"))
    parser.add_argument("--diffset-root", type=Path, default=Path(".cache/reviews/diffsets"))
    return parser.parse_args()


def load_items_jsonl(path: Path) -> list[dict]:
    items: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def main() -> int:
    args = parse_args()
    root = repo_root()

    previous_feedback_path = root / args.feedback_dir / f"{args.previous_diffset_id}.yaml"
    if not previous_feedback_path.exists():
        print(
            "[reconcile][error] previous feedback missing: "
            f"{previous_feedback_path.relative_to(root)}"
        )
        return EXIT_RUNTIME_FAIL

    current_bundle = root / args.diffset_root / args.current_diffset_id
    current_items_path = current_bundle / "items.jsonl"
    if not current_items_path.exists():
        print(
            "[reconcile][error] current diffset items missing: "
            f"{current_items_path.relative_to(root)}"
        )
        return EXIT_RUNTIME_FAIL

    feedback_payload = read_yaml(previous_feedback_path) or {}
    previous_items = {
        str(item.get("item_id")): item
        for item in feedback_payload.get("items", [])
        if isinstance(item, dict) and str(item.get("item_id") or "").strip()
    }
    current_items = load_items_jsonl(current_items_path)

    carried_items = []
    for item in current_items:
        item_id = str(item.get("item_id") or "").strip()
        if not item_id:
            continue
        previous = previous_items.get(item_id)
        if previous is None:
            continue
        carried_items.append(
            {
                "item_id": item_id,
                "verdict": str(previous.get("verdict") or "").strip(),
                "comment": str(previous.get("comment") or ""),
                "status": str(previous.get("status") or "open"),
                "updated_at": utc_now(),
            }
        )

    state_payload = {
        "version": 1,
        "diffset_id": args.current_diffset_id,
        "reviewer": str(feedback_payload.get("reviewer") or ""),
        "reviewed_at": utc_now(),
        "items": carried_items,
    }
    write_json(current_bundle / "review_state.json", state_payload)

    print(
        "[reconcile] wrote review_state "
        f"{(current_bundle / 'review_state.json').relative_to(root)} "
        f"carried_items={len(carried_items)}"
    )
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
