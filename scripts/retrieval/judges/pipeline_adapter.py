from __future__ import annotations

from pathlib import Path
from typing import Any

from retrieval.gates.go_no_go import compute_go_no_go, evaluate_gates
from retrieval.judges import STAGE_B_JUDGES, run_stage_b_judges
from retrieval.services.utils import _read_jsonl, _write_json


def execute_stage_b_pipeline(
    *,
    run_dir: Path,
    root: Path,
    run_id: str,
    draft_rows: list[dict[str, Any]],
    judge_model: str,
    publishable_blocked: bool,
    evidence_gate_status: str,
    citation_resolution_status: str,
    conformance_report: dict[str, Any],
    shape_all: bool,
    core_gate: list[Any],
    rust_gate: list[Any],
) -> dict[str, Any]:
    stage_b_judges = list(STAGE_B_JUDGES)
    stage_b_report = run_stage_b_judges(
        run_dir=run_dir,
        contracts_path=root / "config" / "s0" / "judge_prompt_contracts.yaml",
        scope_report_path=None,
        judge_mode="llm",
        model=judge_model,
    )
    per_target_rows = (
        stage_b_report.get("per_target", []) if isinstance(stage_b_report, dict) else []
    )
    if not isinstance(per_target_rows, list):
        per_target_rows = []

    judge_results: list[dict[str, Any]] = []
    covered_draft_ids: set[str] = set()
    for row in per_target_rows:
        if not isinstance(row, dict):
            continue
        draft_id = str(row.get("draft_id", "")).strip()
        target_id = str(row.get("target_id", "")).strip()
        prompt_id = str(row.get("prompt_id", "")).strip()
        verdict = str(row.get("verdict", "blocked")).strip().lower() or "blocked"
        verdict_rows = row.get("judge_verdicts", [])
        if not isinstance(verdict_rows, list):
            verdict_rows = []
        per_judge = {
            str(item.get("judge", "")).strip(): str(item.get("verdict", "fail")).strip().lower()
            for item in verdict_rows
            if isinstance(item, dict) and str(item.get("judge", "")).strip()
        }
        pass_count = len([value for value in per_judge.values() if value == "pass"])
        judge_results.append(
            {
                "draft_id": draft_id,
                "target_id": target_id,
                "prompt_id": prompt_id,
                "verdict": "candidate" if verdict == "candidate" else "blocked",
                "judge_decisions": per_judge,
                "evidence_grounding": per_judge.get("technical_accuracy") == "pass",
                "utility_complete": pass_count >= 2,
                "significance": 4 if verdict == "candidate" else 2,
                "diagnostic_only": bool(verdict != "candidate"),
            }
        )
        if draft_id:
            covered_draft_ids.add(draft_id)

    for draft in draft_rows:
        if not isinstance(draft, dict):
            continue
        draft_id = str(draft.get("draft_id", "")).strip()
        if not draft_id or draft_id in covered_draft_ids:
            continue
        draft_status = str(draft.get("status", "")).strip().lower()
        if draft_status not in {"abstain", "diagnostic"}:
            continue
        judge_results.append(
            {
                "draft_id": draft_id,
                "target_id": str(draft.get("target_id", "")).strip(),
                "prompt_id": str(draft.get("target_prompt_id", "")).strip(),
                "verdict": "abstain" if draft_status == "abstain" else "diagnostic",
                "judge_decisions": {},
                "evidence_grounding": False,
                "utility_complete": False,
                "significance": 0,
                "diagnostic_only": True,
            }
        )

    judge_trace_rows = _read_jsonl(run_dir / "judge_invocation_trace.jsonl")
    _write_json(
        run_dir / "stage_b_judge_invocations.json",
        {"run_id": run_id, "invocations": judge_trace_rows},
    )

    (run_dir / "stage_b_judges").mkdir(parents=True, exist_ok=True)
    judge_passes = run_dir / "judge_passes"
    judge_passes.mkdir(parents=True, exist_ok=True)
    _write_json(
        judge_passes / "evidence_auditor.json",
        {
            "run_id": run_id,
            "status": "pass" if stage_b_report.get("status") == "pass" else "fail",
            "results": judge_results,
            "notes": "Stage B report mapped from canonical retrieval.judges implementation.",
        },
    )
    _write_json(
        judge_passes / "holistic_pairwise.json",
        {
            "run_id": run_id,
            "status": "diagnostic",
            "stage_c_diagnostic_only": True,
            "notes": "Stage C diagnostic remains excluded from enforcement pass/fail calculation.",
        },
    )

    candidate_grade_count = int(stage_b_report.get("candidate_grade_count", 0) or 0)
    review_count = int(stage_b_report.get("review_count", 0) or 0)
    abstain_rate = float(stage_b_report.get("abstain_rate", 0.0) or 0.0)
    output_conformance_status = str(conformance_report.get("status", "fail"))
    embarrassing_failure_count = int(len(core_gate) + len(rust_gate))

    discrimination_summary: dict[str, dict[str, Any]] = {}
    for judge_name in stage_b_judges:
        observed = [
            str(row.get("judge_decisions", {}).get(judge_name, "")).strip().lower()
            for row in judge_results
            if isinstance(row, dict) and isinstance(row.get("judge_decisions"), dict)
        ]
        pass_count = len([value for value in observed if value == "pass"])
        fail_count = len([value for value in observed if value == "fail"])
        abstain_count = len([value for value in observed if value == "abstain"])
        total = len(observed)
        discrimination_summary[judge_name] = {
            "pass_count": pass_count,
            "fail_count": fail_count,
            "abstain_count": abstain_count,
            "pass_rate": round(pass_count / float(total), 4) if total else 0.0,
            "positive_vs_known_bad_delta": None,
        }

    gate_eval = evaluate_gates(
        evidence_gate_status=evidence_gate_status,
        citation_resolution_status=citation_resolution_status,
        output_conformance_status=output_conformance_status,
        candidate_grade_count=candidate_grade_count,
        review_count=review_count,
        abstain_rate=abstain_rate,
        scope_blocked_count=int(stage_b_report.get("scope_blocked_count", 0) or 0),
    )
    gate_eval["publishable_blocked"] = bool(publishable_blocked)
    gate_eval["shape_all_non_abstain_pass"] = bool(shape_all)
    gate_passed = (
        bool(compute_go_no_go(gate_eval).get("status") == "pass")
        and not publishable_blocked
        and shape_all
    )

    _write_json(
        run_dir / "judge_aggregate.json",
        {
            "run_id": run_id,
            "status": "pass" if gate_passed else "fail",
            "results": judge_results,
            "candidate_grade_count": candidate_grade_count,
            "review_count": review_count,
            "abstain_rate": abstain_rate,
            "evidence_gate_status": evidence_gate_status,
            "citation_resolution_status": citation_resolution_status,
            "output_conformance_status": output_conformance_status,
            "publishable_blocked": publishable_blocked,
            "embarrassing_failure_count": embarrassing_failure_count,
            "discrimination_summary": discrimination_summary,
            "stage_c_diagnostic_only": True,
            "scope_blocked_count": int(stage_b_report.get("scope_blocked_count", 0) or 0),
            "judge_mode": stage_b_report.get("judge_mode", "llm"),
            "judge_invocation_success_rate": stage_b_report.get(
                "judge_invocation_success_rate", 0.0
            ),
        },
    )
    _write_json(
        run_dir / "standalone_judge_aggregate.json",
        {
            "run_id": run_id,
            "status": str(stage_b_report.get("status", "fail")),
            "results": judge_results,
            "per_target": per_target_rows,
            "drafts": stage_b_report.get("drafts", []),
            "candidate_grade_count": candidate_grade_count,
            "review_count": review_count,
            "abstain_rate": abstain_rate,
            "discrimination_summary": discrimination_summary,
        },
    )

    return {
        "stage_b_judges": stage_b_judges,
        "candidate_grade_count": candidate_grade_count,
        "review_count": review_count,
        "abstain_rate": abstain_rate,
        "embarrassing_failure_count": embarrassing_failure_count,
        "gate_passed": gate_passed,
    }
