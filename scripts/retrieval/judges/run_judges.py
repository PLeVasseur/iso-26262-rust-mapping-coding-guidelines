"""Run standalone Step 4 judges on re-rendered RST artifacts."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - direct script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from retrieval.judges.stage_b import (
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


def _handle_missing_verdicts(
    target_id: str,
    per_judge_decisions: dict[str, str],
    expected_judges: list[str],
) -> tuple[dict[str, str], list[str], bool]:
    warnings: list[str] = []
    filled = dict(per_judge_decisions)
    for judge_name in expected_judges:
        if judge_name not in filled:
            filled[judge_name] = "fail"
            warnings.append(
                f"Judge {judge_name} verdict MISSING for {target_id} (silent exit). Treated as fail."
            )

    missing_count = len(expected_judges) - len(per_judge_decisions)
    missing_ratio = (missing_count / len(expected_judges)) if expected_judges else 0.0
    to_diagnostic_lane = missing_ratio > 0.50
    if to_diagnostic_lane:
        warnings.append(
            f"DIAGNOSTIC_LANE: >50% of judge verdicts missing for {target_id} "
            f"({missing_count}/{len(expected_judges)})."
        )

    return filled, warnings, to_diagnostic_lane


def _append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def run_judges(
    run_dir: Path,
    contracts_path: Path,
    scope_report_path: Path | None = None,
    *,
    judge_mode: str = "llm",
    model: str | None = None,
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
    trace_rows: list[dict[str, Any]] = []
    warning_rows: list[dict[str, Any]] = []
    invocation_errors: list[dict[str, Any]] = []
    scope_blocked_count = 0
    invocation_success_count = 0
    invocation_total_count = 0

    progress_path = run_dir / "judge_runner_progress.jsonl"
    if progress_path.exists():
        progress_path.unlink()

    eligible_target_count = 0
    for rst_path in sorted((run_dir / "rerendered_rst").glob("*.rst")):
        prompt_id = _prompt_from_file(rst_path)
        draft = drafts_by_prompt.get(_prompt_key(prompt_id), {})
        if str(draft.get("status", "")).strip().lower() == "abstain":
            continue
        if scope_by_prompt.get(_prompt_key(prompt_id), True):
            eligible_target_count += 1
    expected_total_calls = eligible_target_count * len(STAGE_B_JUDGES)

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
        for judge_index, judge_name in enumerate(STAGE_B_JUDGES, start=1):
            invocation_total_count += 1
            print(
                f"judges: call {invocation_total_count}/{expected_total_calls} ({judge_name} on {prompt_id})",
                file=sys.stderr,
                flush=True,
            )
            _append_jsonl(
                progress_path,
                {
                    "type": "judge_call_start",
                    "timestamp": time.time(),
                    "global_call_index": invocation_total_count,
                    "global_call_total": expected_total_calls,
                    "judge_index": judge_index,
                    "judge_total": len(STAGE_B_JUDGES),
                    "prompt_id": prompt_id,
                    "target_id": str(draft.get("target_id", "")).strip(),
                    "judge": judge_name,
                },
            )
            call_start = time.time()
            try:
                verdict = evaluate_judge(
                    judge_name=judge_name,
                    rst_content=rst_content,
                    construct_terms=construct_terms,
                    contracts=contracts,
                    judge_mode=judge_mode,
                    model=model,
                )
            except Exception as exc:  # pragma: no cover - defensive path
                error = {
                    "target_id": str(draft.get("target_id", "")).strip(),
                    "prompt_id": str(draft.get("target_prompt_id", prompt_id)).strip() or prompt_id,
                    "judge": judge_name,
                    "error": f"judge_invocation_exception:{type(exc).__name__}",
                }
                invocation_errors.append(error)
                warning_rows.append({**error, "warning": error["error"]})
                _append_jsonl(
                    progress_path,
                    {
                        "type": "judge_call_end",
                        "timestamp": time.time(),
                        "global_call_index": invocation_total_count,
                        "prompt_id": prompt_id,
                        "judge": judge_name,
                        "ok": False,
                        "duration_ms": int((time.time() - call_start) * 1000),
                        "error": error["error"],
                    },
                )
                continue
            decision = str(verdict.get("decision", "fail")).strip().lower() or "fail"
            reason_codes = [str(item) for item in verdict.get("reason_codes", []) if str(item)]
            transport_failure = any(
                token in code
                for token in ("judge_transport_failure", "judge_output_empty")
                for code in reason_codes
            )
            if judge_mode == "llm" and transport_failure:
                invocation_errors.append(
                    {
                        "target_id": str(draft.get("target_id", "")).strip(),
                        "prompt_id": str(draft.get("target_prompt_id", prompt_id)).strip()
                        or prompt_id,
                        "judge": judge_name,
                        "error": ",".join(reason_codes)
                        if reason_codes
                        else "judge_transport_failure",
                    }
                )
            else:
                invocation_success_count += 1
            per_judge_decisions[judge_name] = decision
            judge_verdict_rows.append(
                {
                    "judge": judge_name,
                    "verdict": decision,
                    "reason_codes": reason_codes,
                    "summary": verdict.get("summary", ""),
                }
            )
            trace_rows.append(
                {
                    "prompt_id": str(draft.get("target_prompt_id", prompt_id)).strip() or prompt_id,
                    "target_id": str(draft.get("target_id", "")).strip(),
                    "judge": judge_name,
                    "judge_mode": verdict.get("judge_mode", judge_mode),
                    "model": verdict.get("model", model or "default"),
                    "prompt_template_id": verdict.get("prompt_template_id", ""),
                    "prompt_hash": verdict.get("prompt_hash", ""),
                    "decision": decision,
                    "reason_codes": reason_codes,
                    "telemetry": verdict.get("telemetry", {}),
                }
            )
            _append_jsonl(
                progress_path,
                {
                    "type": "judge_call_end",
                    "timestamp": time.time(),
                    "global_call_index": invocation_total_count,
                    "prompt_id": prompt_id,
                    "judge": judge_name,
                    "ok": not transport_failure,
                    "duration_ms": int((time.time() - call_start) * 1000),
                    "decision": decision,
                    "reason_codes": reason_codes,
                },
            )

        if judge_mode == "heuristic":
            per_judge_decisions, missing_warnings, force_diagnostic_lane = _handle_missing_verdicts(
                target_id=str(draft.get("target_id", "")).strip() or prompt_id,
                per_judge_decisions=per_judge_decisions,
                expected_judges=STAGE_B_JUDGES,
            )
        else:
            missing_warnings = []
            force_diagnostic_lane = False
            missing_judges = [name for name in STAGE_B_JUDGES if name not in per_judge_decisions]
            if missing_judges:
                invocation_errors.append(
                    {
                        "target_id": str(draft.get("target_id", "")).strip(),
                        "prompt_id": str(draft.get("target_prompt_id", prompt_id)).strip()
                        or prompt_id,
                        "judge": ",".join(missing_judges),
                        "error": "missing_judge_verdicts",
                    }
                )
                for judge_name in missing_judges:
                    per_judge_decisions[judge_name] = "fail"
        for warning in missing_warnings:
            warning_rows.append(
                {
                    "target_id": str(draft.get("target_id", "")).strip(),
                    "prompt_id": str(draft.get("target_prompt_id", prompt_id)).strip() or prompt_id,
                    "warning": warning,
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
            diagnostic_reason = f"failed judges: {', '.join(failed)}"
            if force_diagnostic_lane:
                diagnostic_reason = "missing_judge_verdicts_over_50pct"
            triage_rows.append(
                {
                    "target_id": str(draft.get("target_id", "")).strip(),
                    "prompt_id": str(draft.get("target_prompt_id", prompt_id)).strip() or prompt_id,
                    "disposition": "diagnostic",
                    "diagnostic_reason": diagnostic_reason,
                    "judge_verdicts": per_judge_decisions,
                }
            )

    candidate_grade_count = sum(1 for row in per_target if row.get("verdict") == "candidate")
    blocked_count = sum(1 for row in per_target if row.get("verdict") == "blocked")
    diagnostic_count = sum(1 for row in triage_rows if row.get("disposition") == "diagnostic")

    trace_path = run_dir / "judge_invocation_trace.jsonl"
    trace_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in trace_rows)
        + ("\n" if trace_rows else ""),
        encoding="utf-8",
    )

    invocation_success_rate = (
        float(invocation_success_count) / float(invocation_total_count)
        if invocation_total_count
        else 0.0
    )
    llm_hard_failure = judge_mode == "llm" and (
        invocation_success_count != invocation_total_count or bool(invocation_errors)
    )

    report = {
        "run_id": run_dir.name,
        "status": (
            "error" if llm_hard_failure else ("pass" if candidate_grade_count >= 1 else "fail")
        ),
        "judge_mode": judge_mode,
        "model": model or "default",
        "prompt_contract_version": contracts.get("contract_version"),
        "prompt_contract_usage_trace_present": trace_path.exists() and bool(trace_rows),
        "prompt_contract_usage_trace_path": str(trace_path),
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
        "warnings": warning_rows,
        "llm_invocation_errors": invocation_errors,
        "judge_invocation_success_count": invocation_success_count,
        "judge_invocation_total_count": invocation_total_count,
        "judge_invocation_success_rate": round(invocation_success_rate, 4),
        "verdict_triage_applied": True,
    }

    out_path = run_dir / "standalone_judge_aggregate.json"
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    progress_summary = {
        "status": report["status"],
        "judge_mode": judge_mode,
        "judge_invocation_success_count": invocation_success_count,
        "judge_invocation_total_count": invocation_total_count,
        "judge_invocation_success_rate": round(invocation_success_rate, 4),
        "llm_invocation_error_count": len(invocation_errors),
    }
    (run_dir / "judge_runner_progress_summary.json").write_text(
        json.dumps(progress_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
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
    parser.add_argument("--judge-mode", choices=["llm", "heuristic"], default="llm")
    parser.add_argument("--model", type=str, default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = run_judges(
        run_dir=args.run_dir.expanduser().resolve(),
        contracts_path=args.judge_contracts.expanduser().resolve(),
        scope_report_path=args.scope_report.expanduser().resolve() if args.scope_report else None,
        judge_mode=args.judge_mode,
        model=args.model or None,
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
    if report["judge_mode"] == "llm" and report.get("status") == "error":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
