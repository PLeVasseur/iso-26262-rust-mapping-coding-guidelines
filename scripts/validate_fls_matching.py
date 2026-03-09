"""Validate grounding-only WS6 runtime abstention behavior."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from context.exemplars import EXEMPLAR_MANIFEST

    from retrieval.writer_host.fls_calibration import (
        evaluate_calibration_items,
        load_calibration_items,
    )
except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(PROJECT_ROOT))
    from context.exemplars import EXEMPLAR_MANIFEST

    from retrieval.writer_host.fls_calibration import (
        evaluate_calibration_items,
        load_calibration_items,
    )


GUIDELINES_REPO = Path(
    os.environ.get(
        "GUIDELINES_REPO", "/Users/pete.levasseur/personal/safety-critical-rust-coding-guidelines"
    )
)
DEFAULT_OUTPUT = Path(".cache/sqlite_kb/reports/fls_grounding_runtime_validation.json")


def run_validation(*, dataset_path: Path | None = None, sweep: bool = False) -> dict[str, Any]:
    if dataset_path is not None and not dataset_path.exists():
        raise RuntimeError(f"dataset path does not exist: {dataset_path}")
    if sweep:
        raise RuntimeError(
            "WS7_REQUIRED: ranking threshold sweep is disabled while runtime remains grounding-only"
        )
    items = load_calibration_items(
        manifest_path=EXEMPLAR_MANIFEST,
        guidelines_repo_root=GUIDELINES_REPO,
        dataset_path=dataset_path,
    )
    report = {
        "dataset_path": str(dataset_path) if dataset_path else "<exemplar_manifest>",
        "runtime_mode": "grounding_only_ws6",
        "non_authoritative_for_ws7": True,
        "item_count": len(items),
        "baseline": evaluate_calibration_items(items=items),
    }
    return report


def resolve_output_path(*, run_dir: Path | None = None, output_path: Path | None = None) -> Path:
    if output_path is not None:
        return output_path
    if run_dir is not None:
        return run_dir / "fls_grounding_runtime_validation.json"
    return DEFAULT_OUTPUT


def write_validation_report(
    report: dict[str, Any], *, run_dir: Path | None = None, output_path: Path | None = None
) -> Path:
    out = resolve_output_path(run_dir=run_dir, output_path=output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate grounding-only WS6 runtime abstention")
    parser.add_argument("--dataset", default="", help="Optional calibration dataset JSON path")
    parser.add_argument("--sweep", action="store_true", help="Disabled until WS7")
    parser.add_argument(
        "--run-dir",
        default="",
        help="Optional run directory; writes <run_dir>/fls_grounding_runtime_validation.json",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional explicit output path; overrides --run-dir and default cache location",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dataset_raw = str(args.dataset or "").strip()
    dataset = Path(dataset_raw).resolve() if dataset_raw else None
    run_dir_raw = str(getattr(args, "run_dir", "") or "").strip()
    output_raw = str(getattr(args, "output", "") or "").strip()
    run_dir = Path(run_dir_raw).resolve() if run_dir_raw else None
    output_path = Path(output_raw).resolve() if output_raw else None
    report = run_validation(dataset_path=dataset, sweep=bool(args.sweep))

    baseline_raw = report.get("baseline")
    baseline: dict[str, Any] = baseline_raw if isinstance(baseline_raw, dict) else {}
    total = int(baseline.get("total", 0) or 0)
    ws7_required = int(baseline.get("ws7_required", 0) or 0)
    abstention_correct = int(baseline.get("abstention_correct", 0) or 0)
    structurally_valid = int(baseline.get("structurally_valid", 0) or 0)
    print("FLS Grounding Runtime Abstention Validation")
    print(f"  Items: {total}")
    print(f"  Runtime mode: {report.get('runtime_mode', 'unknown')}")
    print(f"  WS7 required: {ws7_required}/{total}")
    print(f"  Structurally valid packets: {structurally_valid}/{total}")
    print(f"  Correct abstentions: {abstention_correct}/{total}")

    out = write_validation_report(report, run_dir=run_dir, output_path=output_path)
    print(f"Report saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
