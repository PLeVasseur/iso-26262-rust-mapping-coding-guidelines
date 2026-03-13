from __future__ import annotations

from pathlib import Path
from typing import Any

from retrieval.validation.conformance import validate_rst_conformance


def validate_shape(
    rst_path: Path, *, guideline_id: str = "", convention_spec: dict[str, Any] | None = None
) -> dict[str, Any]:
    valid, violations = validate_rst_conformance(
        rst_path,
        guideline_id=guideline_id,
        convention_spec=convention_spec,
    )
    return {
        "file": rst_path.name,
        "shape_match": valid,
        "candidate_shape_ok": valid,
        "violation_count": len(violations),
        "violations": violations,
    }
