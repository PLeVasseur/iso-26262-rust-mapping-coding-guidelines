"""Compatibility wrapper for WS7 FLS validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from retrieval.writer_host.fls_calibration import (
        extract_fls_ids_from_rst,
        extract_topic_from_rst,
    )
except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(PROJECT_ROOT))
    from retrieval.writer_host.fls_calibration import (
        extract_fls_ids_from_rst,
        extract_topic_from_rst,
    )

import validate_fls_ws7

__all__ = [
    "extract_fls_ids_from_rst",
    "extract_topic_from_rst",
    "run_validation",
    "resolve_output_path",
    "write_validation_report",
]


DEFAULT_OUTPUT = Path(".cache/sqlite_kb/reports/ws7_validation.json")


def run_validation(*, dataset_path: Path | None = None, sweep: bool = False) -> dict[str, Any]:
    if sweep:
        raise RuntimeError(
            "threshold sweep is not implemented for ws7_staged_retrieval_v1; "
            "use validate_fls_ws7 policy-driven validation instead"
        )
    report = validate_fls_ws7.run_validation(dataset_path=dataset_path)
    return {
        **report,
        "compatibility_wrapper": True,
        "deprecated_script": "validate_fls_matching.py",
        "canonical_script": "validate_fls_ws7.py",
    }


def resolve_output_path(*, run_dir: Path | None = None, output_path: Path | None = None) -> Path:
    if output_path is not None:
        return output_path
    if run_dir is not None:
        return run_dir / "ws7_validation.json"
    return DEFAULT_OUTPUT


def write_validation_report(
    report: dict[str, Any], *, run_dir: Path | None = None, output_path: Path | None = None
) -> Path:
    out = resolve_output_path(run_dir=run_dir, output_path=output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compatibility wrapper for WS7 FLS validation")
    parser.add_argument("--dataset", default="", help="Optional calibration dataset JSON path")
    parser.add_argument("--sweep", action="store_true", help="Not implemented for WS7")
    parser.add_argument(
        "--run-dir",
        default="",
        help="Optional run directory; writes <run_dir>/ws7_validation.json",
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
    print("FLS WS7 Validation Compatibility Wrapper")
    print(f"  Runtime mode: {report.get('runtime_mode', 'unknown')}")
    print(f"  Item count: {report.get('item_count', 0)}")
    print(f"  Proof valid: {report.get('proof_valid', False)}")
    out = write_validation_report(report, run_dir=run_dir, output_path=output_path)
    print(f"Report saved: {out}")
    return 0 if bool(report.get("proof_valid", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
