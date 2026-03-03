from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.judges_v2.run_judges import run_judges
from scripts.judges_v2.stage_b import STAGE_B_JUDGES


def run_stage_b_judges(
    run_dir: Path,
    contracts_path: Path,
    scope_report_path: Path | None = None,
    *,
    judge_mode: str = "llm",
    model: str | None = None,
) -> dict[str, Any]:
    return run_judges(
        run_dir=run_dir,
        contracts_path=contracts_path,
        scope_report_path=scope_report_path,
        judge_mode=judge_mode,
        model=model,
    )


def _aggregate_verdicts(per_judge_decisions: dict[str, str]) -> str:
    decisions = [
        str(per_judge_decisions.get(name, "fail")).strip().lower() for name in STAGE_B_JUDGES
    ]
    return "candidate" if all(decision == "pass" for decision in decisions) else "blocked"


def _parse_judge_output(raw: dict[str, Any]) -> tuple[str, str, list[str]]:
    decision = str(raw.get("decision", "fail")).strip().lower()
    if decision == "abstain":
        decision = "fail"
    if decision not in {"pass", "fail"}:
        decision = "fail"
    summary = str(raw.get("summary", "")).strip()
    reason_codes = [str(item) for item in raw.get("reason_codes", []) if str(item)]
    return decision, summary, reason_codes


__all__ = [
    "STAGE_B_JUDGES",
    "run_stage_b_judges",
    "_aggregate_verdicts",
    "_parse_judge_output",
]
