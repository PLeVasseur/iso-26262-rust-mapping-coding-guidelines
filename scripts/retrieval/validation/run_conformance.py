"""Run conformance validation on re-rendered RST files."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - direct script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from retrieval.validation.conformance import (
        validate_batch_conformance,
        validate_rst_conformance,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script invocation
    from conformance import validate_batch_conformance, validate_rst_conformance


def run_conformance_on_rerendered(
    run_dir: Path,
    convention_spec_path: Path | None = None,
) -> dict[str, Any]:
    rst_dir = run_dir / "rerendered_rst"
    if not rst_dir.exists():
        raise RuntimeError(
            f"No rerendered_rst/ directory in {run_dir}. "
            "Run rerender_from_artifacts.py first (Step 2)."
        )

    convention_spec = None
    spec_path = convention_spec_path or (run_dir / "convention_spec.json")
    if spec_path.exists():
        convention_spec = json.loads(spec_path.read_text(encoding="utf-8"))

    conformance_results: list[dict[str, Any]] = []
    for rst_path in sorted(rst_dir.glob("*.rst")):
        text = rst_path.read_text(encoding="utf-8")
        gui_match = re.search(r":id:\s+(gui_\S+)", text)
        guideline_id = gui_match.group(1) if gui_match else ""

        is_valid, violations = validate_rst_conformance(
            rst_path,
            guideline_id,
            convention_spec=convention_spec,
        )

        cat_match = re.search(r":category:\s+(\S+)", text)
        ids_found = re.findall(r":id:\s+(\S+)", text)

        conformance_results.append(
            {
                "file": rst_path.name,
                "prompt_id": rst_path.stem.upper().replace("-", "_"),
                "valid": is_valid,
                "violation_count": len(violations),
                "violations": violations,
                "category": cat_match.group(1) if cat_match else None,
                "ids_found": ids_found,
            }
        )

    batch_valid, batch_violations = validate_batch_conformance(conformance_results)
    status = (
        "pass"
        if conformance_results and all(r["valid"] for r in conformance_results) and batch_valid
        else "fail"
    )

    report = {
        "run_dir": str(run_dir),
        "source": "rerendered_rst",
        "status": status,
        "per_file": conformance_results,
        "batch_violations": batch_violations,
    }

    output_path = run_dir / "output_conformance_report.json"
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate rerendered RST conformance")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--convention-spec", default=None, type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = run_conformance_on_rerendered(
        run_dir=args.run_dir.expanduser().resolve(),
        convention_spec_path=(
            args.convention_spec.expanduser().resolve() if args.convention_spec else None
        ),
    )
    print(json.dumps({"status": report["status"], "files": len(report["per_file"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
