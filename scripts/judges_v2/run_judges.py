"""Run standalone Step 4 judges on re-rendered RST artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - direct script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.judges_v2.stage_b import (
    STAGE_B_JUDGES,
    _compute_verdict,
    evaluate_judge,
    load_judge_contracts,
)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def _prompt_from_file(path: Path) -> str:
    return path.stem.upper()


def _prompt_key(value: str) -> str:
    return value.strip().upper().replace("-", "_")


def run_judges(
    run_dir: Path,
    contracts_path: Path,
    scope_report_path: Path | None = None,
) -> dict[str, Any]:
    contracts = load_judge_contracts(contracts_path)
    drafts = _load_jsonl(run_dir / "drafts.jsonl")
    evidence_rows = _load_jsonl(run_dir / "writer_subagent_outputs" / "evidence_synthesizer.jsonl")
    evidence_by_prompt: dict[str, dict[str, Any]] = {}
    evidence_by_target: dict[str, dict[str, Any]] = {}
    for row in evidence_rows:
        prompt_key = (
            str(row.get("prompt_id", "")).strip() or str(row.get("target_prompt_id", "")).strip()
        )
        target_key = str(row.get("target_id", "")).strip()
        if prompt_key:
            evidence_by_prompt[_prompt_key(prompt_key)] = row
        if target_key:
            evidence_by_target[target_key] = row
    scope = (
        _load_json(scope_report_path)
        if scope_report_path
        else _load_json(run_dir / "scope_cardinality_report.json")
    )
    scope_by_prompt = {
        _prompt_key(str(row.get("prompt_id", "")).strip()): bool(row.get("passed", True))
        for row in scope.get("results", [])
        if isinstance(row, dict)
    }

    drafts_by_prompt = {
        _prompt_key(str(row.get("target_prompt_id", row.get("prompt_id", ""))).strip()): row
        for row in drafts
        if isinstance(row, dict)
    }

    per_target: list[dict[str, Any]] = []
    triage_rows: list[dict[str, Any]] = []
    scope_blocked_count = 0

    rerender_dir = run_dir / "rerendered_rst"
    for rst_path in sorted(rerender_dir.glob("*.rst")):
        prompt_id = _prompt_from_file(rst_path)
        draft = drafts_by_prompt.get(_prompt_key(prompt_id), {})
        if str(draft.get("status", "")).strip().lower() == "abstain":
            continue

        scope_passed = scope_by_prompt.get(_prompt_key(prompt_id), True)
        if not scope_passed:
            scope_blocked_count += 1
            triage_rows.append(
                {
                    "target_id": str(draft.get("target_id", "")).strip(),
                    "prompt_id": str(draft.get("target_prompt_id", prompt_id)).strip() or prompt_id,
                    "disposition": "diagnostic",
                    "diagnostic_reason": "scope_blocked",
                    "judge_verdicts": {},
                }
            )
            continue

        rst_content = rst_path.read_text(encoding="utf-8")
        evidence = evidence_by_prompt.get(_prompt_key(prompt_id), {})
        if not evidence:
            evidence = evidence_by_target.get(str(draft.get("target_id", "")).strip(), {})
        construct_terms = [
            str(item).strip()
            for item in evidence.get("construct_terms", draft.get("construct_terms", []))
            if str(item).strip()
        ]

        per_judge_decisions: dict[str, str] = {}
        judge_verdict_rows: list[dict[str, Any]] = []
        for judge_name in STAGE_B_JUDGES:
            verdict = evaluate_judge(
                judge_name=judge_name,
                rst_content=rst_content,
                construct_terms=construct_terms,
                contracts=contracts,
            )
            decision = str(verdict.get("decision", "fail")).strip().lower() or "fail"
            per_judge_decisions[judge_name] = decision
            judge_verdict_rows.append(
                {
                    "judge": judge_name,
                    "verdict": decision,
                    "reason_codes": verdict.get("reason_codes", []),
                    "summary": verdict.get("summary", ""),
                }
            )

        aggregate_verdict = _compute_verdict(per_judge_decisions)
        per_target.append(
            {
                "target_id": str(draft.get("target_id", "")).strip(),
                "prompt_id": str(draft.get("target_prompt_id", prompt_id)).strip() or prompt_id,
                "draft_id": str(draft.get("draft_id", "")).strip(),
                "verdict": aggregate_verdict,
                "judge_verdicts": judge_verdict_rows,
            }
        )

        if aggregate_verdict == "candidate":
            triage_rows.append(
                {
                    "target_id": str(draft.get("target_id", "")).strip(),
                    "prompt_id": str(draft.get("target_prompt_id", prompt_id)).strip() or prompt_id,
                    "disposition": "candidate",
                    "judge_verdicts": per_judge_decisions,
                }
            )
        else:
            failed = [name for name, state in per_judge_decisions.items() if state != "pass"]
            triage_rows.append(
                {
                    "target_id": str(draft.get("target_id", "")).strip(),
                    "prompt_id": str(draft.get("target_prompt_id", prompt_id)).strip() or prompt_id,
                    "disposition": "diagnostic",
                    "diagnostic_reason": f"failed judges: {', '.join(failed)}",
                    "judge_verdicts": per_judge_decisions,
                }
            )

    candidate_grade_count = sum(1 for row in per_target if row.get("verdict") == "candidate")
    blocked_count = sum(1 for row in per_target if row.get("verdict") == "blocked")
    diagnostic_count = sum(1 for row in triage_rows if row.get("disposition") == "diagnostic")

    report = {
        "run_id": run_dir.name,
        "status": "pass" if candidate_grade_count >= 1 else "fail",
        "judge_set": STAGE_B_JUDGES,
        "verdict_model": "binary_pass_fail",
        "candidate_grade_count": candidate_grade_count,
        "candidate_count": candidate_grade_count,
        "blocked_count": blocked_count,
        "scope_blocked_count": scope_blocked_count,
        "review_count": 0,
        "abstain_rate": 0.0,
        "per_target": per_target,
        "drafts": triage_rows,
        "diagnostic_count": diagnostic_count,
        "verdict_triage_applied": True,
    }

    out_path = run_dir / "standalone_judge_aggregate.json"
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run standalone v2 Stage-B judges")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument(
        "--judge-contracts",
        type=Path,
        default=Path("config/s0/judge_prompt_contracts.yaml"),
    )
    parser.add_argument("--scope-report", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = run_judges(
        run_dir=args.run_dir.expanduser().resolve(),
        contracts_path=args.judge_contracts.expanduser().resolve(),
        scope_report_path=args.scope_report.expanduser().resolve() if args.scope_report else None,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "candidate_grade_count": report["candidate_grade_count"],
                "blocked_count": report["blocked_count"],
                "scope_blocked_count": report["scope_blocked_count"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
