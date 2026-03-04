from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from retrieval.services import phase_a_retired

curated_ids: list[str] = []


def _extract_json_object(text: str) -> dict[str, Any]:
    candidate = str(text).strip()
    if candidate.startswith("```"):
        candidate = candidate.removeprefix("```json").removeprefix("```").strip()
        if candidate.endswith("```"):
            candidate = candidate[:-3].strip()
    if candidate.startswith("{") and candidate.endswith("}"):
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(candidate[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("No JSON object found in model response")


def run_scaffold_s0_config(args: Namespace, *, root: Path) -> int:
    return phase_a_retired.run_scaffold_s0_config(args, root=root)


def run_doctor(args: Namespace, *, root: Path) -> int:
    return phase_a_retired.run_doctor(args, root=root)


def run_enumerate_targets(args: Namespace, *, root: Path) -> int:
    return phase_a_retired.run_enumerate_targets(args, root=root)


def run_calibration_run(args: Namespace, *, root: Path) -> int:
    return phase_a_retired.run_calibration_run(args, root=root)


def run_enforce_calibration_quality(args: Namespace, *, root: Path) -> int:
    return phase_a_retired.run_enforce_calibration_quality(args, root=root)


def run_pack_reviewer_packet(args: Namespace, *, root: Path) -> int:
    return phase_a_retired.run_pack_reviewer_packet(args, root=root)
