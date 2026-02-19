#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from _common import EXIT_RUNTIME_FAIL, EXIT_SUCCESS, read_json, read_yaml, repo_root, write_json
from known_good_lib import (
    extract_feature_vector,
    load_manifest,
    save_report,
    summarize_feature,
    utc_now,
    write_jsonl,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build known-good feature baseline statistics")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("benchmarks/known-good/manifest.yaml"),
    )
    parser.add_argument(
        "--alignment-policy",
        type=Path,
        default=Path("config/alignment_policy.yaml"),
    )
    parser.add_argument("--active-tier", choices=["strict", "extended", "all"])
    parser.add_argument(
        "--per-guideline-output",
        type=Path,
        default=Path("benchmarks/known-good/features/per_guideline.jsonl"),
    )
    parser.add_argument(
        "--baseline-output",
        type=Path,
        default=Path("benchmarks/known-good/features/baseline.json"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("benchmarks/known-good/reports/features_report.json"),
    )
    return parser.parse_args()


def is_selected_tier(entry_tier: str, active_tier: str) -> bool:
    if active_tier == "all":
        return entry_tier in {"strict", "extended"}
    if active_tier == "extended":
        return entry_tier in {"strict", "extended"}
    return entry_tier == "strict"


def main() -> int:
    args = parse_args()
    root = repo_root()
    manifest_path = root / args.manifest
    if not manifest_path.exists():
        print(f"[known-good-features][error] missing manifest: {manifest_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL

    alignment_policy = read_yaml(root / args.alignment_policy) or {}
    active_tier = str(args.active_tier or alignment_policy.get("active_tier") or "strict")
    weights = (alignment_policy.get("weights") or {}).copy()

    manifest = load_manifest(manifest_path)
    feature_rows: list[dict[str, Any]] = []
    missing_canonical = 0

    for entry in manifest.get("guidelines", []):
        entry_tier = str(entry.get("tier") or "extended")
        if not is_selected_tier(entry_tier, active_tier):
            continue

        canonical_rel = str(entry.get("local_canonical_path") or "").strip()
        if not canonical_rel:
            missing_canonical += 1
            continue
        canonical_path = root / canonical_rel
        if not canonical_path.exists():
            missing_canonical += 1
            continue

        canonical = read_json(canonical_path)
        features = extract_feature_vector(canonical)
        feature_rows.append(
            {
                "guideline_id": str(
                    canonical.get("guideline_id") or entry.get("guideline_id") or ""
                ),
                "tier": entry_tier,
                "features": features,
            }
        )

    if not feature_rows:
        print("[known-good-features][error] no canonical feature rows found")
        return EXIT_RUNTIME_FAIL

    feature_names = sorted(
        {
            feature_name
            for row in feature_rows
            for feature_name in (row.get("features") or {}).keys()
        }
    )

    feature_stats = {}
    for feature_name in feature_names:
        values = [
            float((row.get("features") or {}).get(feature_name, 0.0)) for row in feature_rows
        ]
        feature_stats[feature_name] = summarize_feature(values)

    write_jsonl(root / args.per_guideline_output, feature_rows)

    baseline_payload = {
        "version": 1,
        "generated_at": utc_now(),
        "source_pack_id": str(manifest.get("pack_id") or "unknown-pack"),
        "active_tier": active_tier,
        "guideline_count": len(feature_rows),
        "feature_stats": feature_stats,
        "dimension_weights": weights,
    }
    write_json(root / args.baseline_output, baseline_payload)

    report = {
        "version": 1,
        "generated_at": utc_now(),
        "manifest": str(args.manifest),
        "active_tier": active_tier,
        "guideline_count": len(feature_rows),
        "missing_canonical_count": missing_canonical,
        "baseline_output": str(args.baseline_output),
        "per_guideline_output": str(args.per_guideline_output),
    }
    save_report(root / args.report, report)

    print(
        "[known-good-features] "
        f"active_tier={active_tier} guidelines={len(feature_rows)} "
        f"missing_canonical={missing_canonical}"
    )
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
