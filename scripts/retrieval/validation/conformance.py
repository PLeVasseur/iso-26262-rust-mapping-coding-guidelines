from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.validation_v2.conformance import validate_batch_conformance, validate_rst_conformance


def validate_generated_rst_conformance(
    run_dir: Path,
    *,
    source_dir_name: str = "generated_guidelines_rst",
    convention_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rst_dir = run_dir / source_dir_name
    results: list[dict[str, Any]] = []
    for rst_path in sorted(rst_dir.glob("*.rst")):
        is_valid, violations = validate_rst_conformance(
            rst_path, guideline_id="", convention_spec=convention_spec
        )
        results.append(
            {
                "file": rst_path.name,
                "prompt_id": rst_path.stem.upper().replace("-", "_"),
                "valid": is_valid,
                "violation_count": len(violations),
                "violations": violations,
            }
        )
    batch_valid, batch_violations = validate_batch_conformance(results)
    status = (
        "pass" if results and all(item["valid"] for item in results) and batch_valid else "fail"
    )
    return {
        "source": source_dir_name,
        "status": status,
        "per_file": results,
        "batch_violations": batch_violations,
    }
