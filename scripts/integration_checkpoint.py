"""Integration checkpoint gate with regression signaling.

Exit codes:
    0 = all checks pass
    1 = regression detected (halt)
    2 = new failure (non-regression; caution)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPORTS_DIR = Path(".cache/sqlite_kb/reports")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _latest_report_dir() -> Path | None:
    if not REPORTS_DIR.exists():
        return None
    dirs = sorted(REPORTS_DIR.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True)
    return dirs[0] if dirs else None


def _load_report(run_dir: Path, name: str) -> dict[str, Any] | None:
    path = run_dir / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _check_gate_status(
    report: dict[str, Any] | None,
    key: str,
    expected: str = "pass",
) -> tuple[str, bool]:
    if report is None:
        return "missing", False
    status = str(report.get(key, report.get("status", "unknown")))
    return status, status == expected


def _standalone_candidate_count(judge_data: dict[str, Any]) -> int:
    if isinstance(judge_data.get("candidate_grade_count"), int):
        return int(judge_data["candidate_grade_count"])
    if isinstance(judge_data.get("candidate_count"), int):
        return int(judge_data["candidate_count"])

    per_target = judge_data.get("per_target", [])
    if isinstance(per_target, list):
        count = 0
        for row in per_target:
            if not isinstance(row, dict):
                continue
            if str(row.get("verdict", "")).strip().lower() == "candidate":
                count += 1
        return count
    return 0


def _standalone_has_non_abstain_for_all_judges(judge_data: dict[str, Any]) -> bool:
    per_target = judge_data.get("per_target", [])
    if not isinstance(per_target, list):
        return False

    for row in per_target:
        if not isinstance(row, dict):
            continue
        judge_rows = row.get("judge_verdicts")
        if not isinstance(judge_rows, list) or len(judge_rows) < 3:
            continue
        verdicts = [str(item.get("verdict", "")).strip().lower() for item in judge_rows]
        if verdicts and all(verdict and verdict != "abstain" for verdict in verdicts):
            return True
    return False


def checkpoint_a(run_dir: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    rerender_dir = run_dir / "rerendered_rst"
    results.append(
        {
            "check": "standalone_rerender_dir_exists",
            "passed": rerender_dir.exists(),
            "detail": str(rerender_dir),
        }
    )

    rst_files = sorted(rerender_dir.glob("*.rst")) if rerender_dir.exists() else []
    results.append(
        {
            "check": "standalone_rerender_has_rst",
            "passed": len(rst_files) >= 1,
            "detail": f"rst_count={len(rst_files)}",
        }
    )

    fabricated_pattern = re.compile(r":id:\s+gui_[0-9a-f]{12}\b")
    has_fabricated = False
    for rst in rst_files:
        if fabricated_pattern.search(_read_text(rst)):
            has_fabricated = True
            break
    results.append(
        {
            "check": "standalone_renderer_ids_not_fabricated",
            "passed": len(rst_files) >= 1 and not has_fabricated,
            "detail": (
                "hex-hash guideline IDs absent in rerendered_rst"
                if rst_files
                else "no rerendered RST files to validate"
            ),
        }
    )

    conformance = _load_report(run_dir, "output_conformance_report.json")
    conformance_per_file = []
    if conformance and isinstance(conformance.get("per_file"), list):
        conformance_per_file = conformance["per_file"]
    has_one_valid = any(
        bool(row.get("valid")) for row in conformance_per_file if isinstance(row, dict)
    )
    valid_file_count = sum(
        1 for row in conformance_per_file if isinstance(row, dict) and row.get("valid")
    )
    file_count = len(conformance_per_file)
    results.append(
        {
            "check": "standalone_conformance_report_exists",
            "passed": conformance is not None,
            "detail": str(run_dir / "output_conformance_report.json"),
        }
    )
    results.append(
        {
            "check": "standalone_conformance_has_passing_file",
            "passed": file_count >= 1,
            "detail": (
                f"file_count={file_count}, valid_file_count={valid_file_count}, "
                f"has_passing_file={has_one_valid}"
            ),
        }
    )

    standalone_judges = _load_report(run_dir, "standalone_judge_aggregate.json")
    results.append(
        {
            "check": "standalone_judge_aggregate_exists",
            "passed": standalone_judges is not None,
            "detail": str(run_dir / "standalone_judge_aggregate.json"),
        }
    )
    if standalone_judges:
        candidates = _standalone_candidate_count(standalone_judges)
        results.extend(
            [
                {
                    "check": "standalone_judge_mode_llm",
                    "passed": str(standalone_judges.get("judge_mode", "")).strip().lower() == "llm",
                    "detail": f"judge_mode={standalone_judges.get('judge_mode', 'unknown')}",
                },
                {
                    "check": "standalone_judges_non_abstain_triplet",
                    "passed": _standalone_has_non_abstain_for_all_judges(standalone_judges),
                    "detail": "at least one target has 3 non-abstain judge verdicts",
                },
                {
                    "check": "standalone_candidate_count_positive",
                    "passed": candidates >= 1,
                    "detail": f"candidates={candidates} (need >=1)",
                },
                {
                    "check": "judge_prompt_contract_usage_trace_present",
                    "passed": bool(standalone_judges.get("prompt_contract_usage_trace_present")),
                    "detail": str(
                        standalone_judges.get("prompt_contract_usage_trace_path", "missing")
                    ),
                },
                {
                    "check": "judge_invocation_success_rate_full",
                    "passed": float(standalone_judges.get("judge_invocation_success_rate", 0.0))
                    >= 1.0,
                    "detail": f"success_rate={standalone_judges.get('judge_invocation_success_rate', 0.0)}",
                },
                {
                    "check": "judge_invocation_error_count_zero",
                    "passed": len(standalone_judges.get("llm_invocation_errors", [])) == 0,
                    "detail": (
                        f"error_count={len(standalone_judges.get('llm_invocation_errors', []))}"
                    ),
                },
            ]
        )

    judge_calibration = _load_report(run_dir, "judge_calibration_report.json")
    results.append(
        {
            "check": "judge_calibration_report_exists",
            "passed": judge_calibration is not None,
            "detail": str(run_dir / "judge_calibration_report.json"),
        }
    )
    if judge_calibration:
        thresholds_met = bool(judge_calibration.get("thresholds_met"))
        results.append(
            {
                "check": "judge_calibration_thresholds_met",
                "passed": thresholds_met,
                "detail": f"thresholds_met={thresholds_met}",
            }
        )

    evidence = _load_report(run_dir, "evidence_synthesizer_gate_report.json")
    status, passed = _check_gate_status(evidence, "status")
    results.append(
        {
            "check": "evidence_gate_status",
            "passed": passed,
            "detail": f"status={status}",
            "regression_if_fail": True,
        }
    )

    citation = _load_report(run_dir, "citation_resolution_report.json")
    status, passed = _check_gate_status(citation, "status")
    results.append(
        {
            "check": "citation_resolution_status",
            "passed": passed,
            "detail": f"status={status}",
            "regression_if_fail": True,
        }
    )

    return results


def checkpoint_b(run_dir: Path) -> list[dict[str, Any]]:
    results = checkpoint_a(run_dir)

    baseline = run_dir / "retrieval_improvement_baseline.json"
    results.append(
        {
            "check": "retrieval_baseline_exists",
            "passed": baseline.exists(),
            "detail": str(baseline),
        }
    )

    baseline_data = _load_report(run_dir, "retrieval_improvement_baseline.json")
    if baseline_data:
        sem_mrr = float(baseline_data.get("semantic_mrr_after", 0.0))
        results.append(
            {
                "check": "semantic_mrr_meets_threshold",
                "passed": sem_mrr >= 0.600,
                "detail": f"semantic_mrr={sem_mrr:.3f} (need >=0.600)",
            }
        )
        hyb_prec = float(baseline_data.get("hybrid_precision_after", 0.0))
        results.append(
            {
                "check": "hybrid_precision_meets_threshold",
                "passed": hyb_prec >= 0.550,
                "detail": f"hybrid_precision={hyb_prec:.3f} (need >=0.550)",
            }
        )

    threshold_review = Path("docs/retrieval_threshold_review.md")
    results.append(
        {
            "check": "threshold_review_exists",
            "passed": threshold_review.exists() and threshold_review.stat().st_size >= 100,
            "detail": f"exists={threshold_review.exists()}",
        }
    )

    artifact_checks = [
        ("output_conformance_report.json", "output conformance"),
        ("code_validation_report.json", "code validation"),
        ("judge_aggregate.json", "judge aggregate"),
        ("role_validation_report.json", "role validation"),
    ]
    for filename, label in artifact_checks:
        path = run_dir / filename
        results.append(
            {
                "check": f"{label.replace(' ', '_')}_exists",
                "passed": path.exists(),
                "detail": str(path),
            }
        )

    spec_path = Path("cache/convention_spec.json")
    results.append(
        {
            "check": "convention_spec_exists",
            "passed": spec_path.exists(),
            "detail": str(spec_path),
        }
    )

    judge_agg = _load_report(run_dir, "judge_aggregate.json")
    if judge_agg:
        candidates = int(judge_agg.get("candidate_grade_count", 0))
        review_count = int(judge_agg.get("review_count", -1))
        abstain_rate = float(judge_agg.get("abstain_rate", 1.0))
        results.extend(
            [
                {
                    "check": "candidate_count_positive",
                    "passed": candidates >= 1,
                    "detail": f"candidates={candidates} (need >=1)",
                },
                {
                    "check": "abstain_rate_acceptable",
                    "passed": abstain_rate <= 0.50,
                    "detail": f"abstain_rate={abstain_rate:.2f} (need <=0.50)",
                },
                {
                    "check": "review_count_zero",
                    "passed": review_count == 0,
                    "detail": f"review_count={review_count} (need =0)",
                },
            ]
        )

    judge_cal = _load_report(run_dir, "judge_calibration_report.json")
    if judge_cal:
        verdicts = judge_cal.get("exemplar_verdicts", [])
        all_pass = all(isinstance(v, dict) and v.get("verdict") == "pass" for v in verdicts)
        results.append(
            {
                "check": "judge_calibration_all_exemplars_pass",
                "passed": all_pass,
                "detail": f"all_exemplars_pass={all_pass}",
            }
        )

    fls_val = _load_report(run_dir, "fls_matching_validation.json")
    if fls_val:
        top1_matches = int(fls_val.get("top1_accuracy", 0))
        results.append(
            {
                "check": "fls_matching_accuracy_acceptable",
                "passed": top1_matches >= 7,
                "detail": f"top1_matches={top1_matches}/14 (need >=7)",
            }
        )

    inv_result = subprocess.run(
        ["uv", "run", "pytest", "tests/test_v3_invariants.py", "-x", "--tb=line", "-q"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    results.append(
        {
            "check": "v3_invariants_suite",
            "passed": inv_result.returncode == 0,
            "detail": inv_result.stdout[-500:] if inv_result.returncode != 0 else "all passed",
            "regression_if_fail": True,
        }
    )

    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", choices=["A", "B", "C"], required=True)
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir or _latest_report_dir()
    if run_dir is None:
        print("ERROR: No report directory found")
        sys.exit(2)

    print(f"=== Integration Checkpoint {args.checkpoint} ===")
    print(f"Run directory: {run_dir}\n")

    if args.checkpoint == "A":
        results = checkpoint_a(run_dir)
    else:
        results = checkpoint_b(run_dir)
    has_regression = False
    has_failure = False

    for result in results:
        icon = "[OK]" if result["passed"] else "[FAIL]"
        print(f"  {icon} {result['check']}: {result['detail']}")
        if not result["passed"]:
            if result.get("regression_if_fail"):
                has_regression = True
                print("    [REGRESSION]")
            else:
                has_failure = True

    print()
    if has_regression:
        print("REGRESSION_DETECTED -- orchestrator must HALT")
        sys.exit(1)
    if has_failure:
        print("New failures detected (not regressions) -- proceed with caution")
        sys.exit(2)

    print(f"Checkpoint {args.checkpoint} PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
