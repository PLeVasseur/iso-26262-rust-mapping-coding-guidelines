#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from _common import (
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    load_guidelines_payload,
    read_yaml,
    repo_root,
    utc_now,
    write_yaml,
)
from _fls_proxy import classify_target_class, normalize_obligation_unit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan target fanout and optional scaffold requests"
    )
    parser.add_argument("--seed-topics", type=Path, default=Path("data/seed_topics.yaml"))
    parser.add_argument("--todo-guidelines", type=Path, default=Path("data/todo_guidelines.yaml"))
    parser.add_argument("--policy", type=Path, default=Path("config/completeness_policy.yaml"))
    parser.add_argument("--output", type=Path, default=Path("data/decomposition_report.yaml"))
    return parser.parse_args()


def seed_target_id(seed: dict[str, Any]) -> str:
    anchor = str(seed.get("citation_anchor_id") or "").strip()
    if anchor:
        return anchor
    chunk = str(seed.get("chunk_id") or "").strip()
    return chunk or str(seed.get("seed_id") or "unknown-target")


def main() -> int:
    args = parse_args()
    root = repo_root()

    seed_topics_path = root / args.seed_topics
    todo_path = root / args.todo_guidelines
    policy_path = root / args.policy

    if not seed_topics_path.exists():
        print(f"[decompose][error] missing seed topics: {seed_topics_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL
    if not todo_path.exists():
        print(f"[decompose][error] missing guidelines: {todo_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL
    if not policy_path.exists():
        print(f"[decompose][error] missing policy: {policy_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL

    seed_payload = read_yaml(seed_topics_path) or {}
    guideline_payload = load_guidelines_payload(todo_path)
    policy = read_yaml(policy_path) or {}

    seeds = seed_payload.get("seed_topics") or []
    guidelines = guideline_payload.get("guidelines") or []
    fanout_policy = policy.get("target_fanout_min_by_target_class") or {}
    apply_scaffolds = bool((policy.get("decomposition") or {}).get("apply_scaffolds", False))
    policy_mode = str((policy.get("gate_modes") or {}).get("decomposition") or "warn")

    seed_by_id: dict[str, dict[str, Any]] = {}
    seed_target_map: dict[str, str] = {}
    seed_obligation_map: dict[str, str] = {}
    seeds_by_target: dict[str, list[str]] = defaultdict(list)

    for seed in seeds:
        seed_id = str(seed.get("seed_id") or "").strip()
        if not seed_id:
            continue
        target_id = seed_target_id(seed)
        obligation_unit_id = str(
            seed.get("obligation_unit_id") or ""
        ).strip() or normalize_obligation_unit(seed)
        seed_by_id[seed_id] = seed
        seed_target_map[seed_id] = target_id
        seed_obligation_map[seed_id] = obligation_unit_id
        seeds_by_target[target_id].append(seed_id)

    target_guidelines: dict[str, set[str]] = defaultdict(set)
    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if not guideline_id:
            continue
        for seed_id in guideline.get("iso_seeds", []) or []:
            sid = str(seed_id).strip()
            target_id = seed_target_map.get(sid)
            if target_id:
                target_guidelines[target_id].add(guideline_id)

    target_metrics = []
    scaffold_requests = []

    for target_id in sorted(seeds_by_target):
        target_class = classify_target_class(target_id)
        required_count = int(fanout_policy.get(target_class, 1))
        current_count = len(target_guidelines.get(target_id, set()))
        shortage = max(0, required_count - current_count)

        target_metrics.append(
            {
                "target_id": target_id,
                "target_class": target_class,
                "guideline_count": current_count,
                "required_guideline_count": required_count,
                "ok": shortage == 0,
            }
        )

        if not apply_scaffolds or shortage == 0:
            continue

        seed_ids = sorted(seeds_by_target[target_id])
        if not seed_ids:
            continue
        primary_seed_id = seed_ids[0]
        obligation_unit_id = seed_obligation_map.get(primary_seed_id, target_id)
        for ordinal in range(1, shortage + 1):
            scaffold_requests.append(
                {
                    "target_id": target_id,
                    "seed_id": primary_seed_id,
                    "obligation_unit_id": obligation_unit_id,
                    "ordinal": ordinal,
                }
            )

    payload = {
        "version": 1,
        "generated_at": utc_now(),
        "policy_mode": policy_mode,
        "target_metrics": target_metrics,
        "scaffold_requests": scaffold_requests,
    }

    output_path = root / args.output
    write_yaml(output_path, payload)
    print(
        "[decompose] wrote "
        f"targets={len(target_metrics)} scaffold_requests={len(scaffold_requests)} "
        f"-> {output_path.relative_to(root)}"
    )
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
