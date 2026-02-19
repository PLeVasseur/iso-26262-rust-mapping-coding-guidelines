#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import EXIT_RUNTIME_FAIL, EXIT_SUCCESS, read_yaml, repo_root, write_yaml
from _fls_proxy import normalize_obligation_unit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill obligation_unit_id in seed topics")
    parser.add_argument("--seed-topics", type=Path, default=Path("data/seed_topics.yaml"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = repo_root()
    seed_topics_path = root / args.seed_topics
    if not seed_topics_path.exists():
        print(f"[seed-obligation-backfill][error] missing: {seed_topics_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL

    payload = read_yaml(seed_topics_path) or {}
    seeds = payload.get("seed_topics") or []
    updated = 0
    for seed in seeds:
        current = str(seed.get("obligation_unit_id") or "").strip()
        if current:
            continue
        seed["obligation_unit_id"] = normalize_obligation_unit(seed)
        updated += 1

    write_yaml(seed_topics_path, payload)
    print(
        "[seed-obligation-backfill] "
        f"updated={updated} total={len(seeds)} -> {seed_topics_path.relative_to(root)}"
    )
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
