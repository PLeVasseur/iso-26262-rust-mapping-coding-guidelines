"""Validate and calibrate FLS matching quality."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from context.exemplars import EXEMPLAR_MANIFEST
    from context.fls_lookup import _effective_policy

    from retrieval.writer_host.fls_calibration import (
        evaluate_calibration_items,
        load_calibration_items,
        run_threshold_sweep,
    )
except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(PROJECT_ROOT))
    from context.exemplars import EXEMPLAR_MANIFEST
    from context.fls_lookup import _effective_policy

    from retrieval.writer_host.fls_calibration import (
        evaluate_calibration_items,
        load_calibration_items,
        run_threshold_sweep,
    )


GUIDELINES_REPO = Path(
    os.environ.get(
        "GUIDELINES_REPO", "/Users/pete.levasseur/personal/safety-critical-rust-coding-guidelines"
    )
)


def run_validation(*, dataset_path: Path | None = None, sweep: bool = False) -> dict[str, Any]:
    items = load_calibration_items(
        manifest_path=EXEMPLAR_MANIFEST,
        guidelines_repo_root=GUIDELINES_REPO,
        dataset_path=dataset_path,
    )
    base_policy = _effective_policy(None)
    report = {
        "dataset_path": str(dataset_path) if dataset_path else "<exemplar_manifest>",
        "item_count": len(items),
        "baseline": evaluate_calibration_items(items=items, policy_overrides=base_policy),
    }
    if sweep:
        report["sweep"] = run_threshold_sweep(items=items, base_policy=base_policy)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and calibrate FLS matching")
    parser.add_argument("--dataset", default="", help="Optional calibration dataset JSON path")
    parser.add_argument("--sweep", action="store_true", help="Run threshold sweep")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dataset_raw = str(args.dataset or "").strip()
    dataset = Path(dataset_raw).resolve() if dataset_raw else None
    report = run_validation(dataset_path=dataset, sweep=bool(args.sweep))

    baseline_raw = report.get("baseline")
    baseline: dict[str, Any] = baseline_raw if isinstance(baseline_raw, dict) else {}
    total = int(baseline.get("total", 0) or 0)
    strict = int(baseline.get("strict_top1", 0) or 0)
    unresolved = int(baseline.get("unresolved", 0) or 0)
    topk_ratio = float(baseline.get("topk_ratio", 0.0) or 0.0)
    print("FLS Matching Calibration")
    print(f"  Items: {total}")
    print(f"  Strict top-1: {strict}/{total}")
    print(f"  Top-k contains: {topk_ratio:.1%}")
    print(f"  Unresolved: {unresolved}/{total}")

    if isinstance(report.get("sweep"), dict):
        best = report["sweep"].get("best") if isinstance(report["sweep"].get("best"), dict) else {}
        if best:
            print("  Sweep best thresholds:")
            print(json.dumps(best.get("thresholds", {}), indent=2, sort_keys=False))

    out = Path(".cache/sqlite_kb/reports/fls_matching_validation.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"Report saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
