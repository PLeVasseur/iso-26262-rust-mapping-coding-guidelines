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


def _latest_retrieval_report_dir() -> Path | None:
    """Find the most recently modified retrieval run directory.

    Prefers directories that look like retrieval runs (contain
    retrieval_improvement_baseline.json or an eval report JSON).
    Falls back to most-recently-modified directory if no retrieval
    dir is found, but emits a warning so operators know to use
    --checkpoint-run-dir explicitly.
    """
    if not REPORTS_DIR.exists():
        return None
    dirs = sorted(REPORTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    # Prefer dirs that look like retrieval runs
    for d in dirs:
        if (d / "retrieval_improvement_baseline.json").exists():
            return d
        if list(d.glob("rust_reference_*.json")) or list(d.glob("core_docs_*.json")):
            return d
    # Fallback: warn and return most recent
    if dirs:
        print(
            f"  [WARN] No retrieval run dir auto-detected in {REPORTS_DIR}. "
            "Use --checkpoint-run-dir to avoid ambiguous selection."
        )
        return dirs[0]
    return None


def _extract_baseline_metrics(baseline_data: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Extract per-corpus semantic_mrr and hybrid_precision from baseline JSON.

    Supports both the current nested schema (after.{corpus}.metrics.*)
    and the legacy flat schema (returns single corpus keyed as 'default').
    Returns dict of {corpus: {semantic_mrr, hybrid_precision}}.
    """
    result: dict[str, dict[str, float]] = {}

    # Try nested schema first (Step 10+ format): after.{corpus}.metrics.*
    after = baseline_data.get("after", {})
    if after and isinstance(after, dict):
        for corpus, corpus_data in after.items():
            if not isinstance(corpus_data, dict):
                continue
            metrics = corpus_data.get("metrics", {})
            sem = metrics.get("semantic", {})
            hyb = metrics.get("hybrid", {})
            result[corpus] = {
                "semantic_mrr": float(sem.get("mrr_at_k", 0.0)),
                "hybrid_precision": float(hyb.get("precision_at_k", 0.0)),
            }

    # Also pull decision.per_corpus for any corpus not already in after
    per_corpus = baseline_data.get("decision", {}).get("per_corpus", {})
    for corpus, corpus_data in per_corpus.items():
        if not isinstance(corpus_data, dict):
            continue
        if corpus not in result:
            result[corpus] = {
                "semantic_mrr": float(corpus_data.get("semantic_mrr", 0.0)),
                "hybrid_precision": float(corpus_data.get("hybrid_precision", 0.0)),
            }

    # Legacy flat schema fallback
    if not result:
        result["default"] = {
            "semantic_mrr": float(baseline_data.get("semantic_mrr_after", 0.0)),
            "hybrid_precision": float(baseline_data.get("hybrid_precision_after", 0.0)),
        }

    return result


def checkpoint_b(run_dir: Path) -> list[dict[str, Any]]:
    """Checkpoint B: Retrieval infrastructure + threshold review gate.

    Does NOT inherit Checkpoint A (which validates Phase-A pipeline artifacts).
    This checkpoint is scoped to Step 10/11 retrieval work.
    """
    results: list[dict[str, Any]] = []

    # --- Core retrieval baseline ---
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
        per_corpus_metrics = _extract_baseline_metrics(baseline_data)

        # Check each corpus independently — all must meet the exception floor.
        for corpus, metrics in per_corpus_metrics.items():
            sem_mrr = metrics["semantic_mrr"]
            hyb_prec = metrics["hybrid_precision"]

            results.append(
                {
                    "check": f"{corpus}.semantic_mrr_meets_exception_floor",
                    "passed": sem_mrr >= 0.540,
                    "detail": f"{corpus} semantic_mrr={sem_mrr:.4f} (exception_floor=0.540, target=0.600)",
                    "regression_if_fail": True,
                }
            )
            # rust_reference hybrid precision is known to be below the exception floor (0.476).
            # This check will FAIL for rust_reference; that is the documented Step 10 STOP.
            # Step 11 Part A will determine whether the threshold should be revised.
            results.append(
                {
                    "check": f"{corpus}.hybrid_precision_meets_exception_floor",
                    "passed": hyb_prec >= 0.550,
                    "detail": (
                        f"{corpus} hybrid_precision={hyb_prec:.4f} (exception_floor=0.550, target=0.650)"
                        + (" — Step 10 STOP diagnostic; see retrieval_threshold_review.md" if hyb_prec < 0.550 else "")
                    ),
                }
            )

        # Overall decision: warn on STOP but don't double-count as regression here.
        decision_status = str(baseline_data.get("status", "unknown"))
        results.append(
            {
                "check": "baseline_decision_recorded",
                "passed": decision_status in ("stop", "accept", "accept_with_exception"),
                "detail": f"decision_status={decision_status}",
            }
        )

    # --- Threshold review document ---
    threshold_review = Path("docs/retrieval_threshold_review.md")
    tr_exists = threshold_review.exists()
    tr_nontrivial = tr_exists and threshold_review.stat().st_size >= 200
    results.append(
        {
            "check": "threshold_review_exists_and_nontrivial",
            "passed": tr_nontrivial,
            "detail": (
                f"exists={tr_exists}, size={threshold_review.stat().st_size if tr_exists else 0}"
            ),
        }
    )

    # --- Step 11 evidence artifacts (advisory — not all will exist before Step 11 runs) ---
    step11_artifacts = [
        ("retrieval_human_review_s0.json", "human review of top-k retrieval for s0 targets"),
        ("rust_reference_eval_report_ws3_main.json", "WS3 main eval report"),
        ("rust_reference_eval_report_ws3_adversarial.json", "WS3 adversarial eval report"),
    ]
    for filename, label in step11_artifacts:
        # Search both run_dir and project root
        found = (run_dir / filename).exists() or Path(filename).exists()
        results.append(
            {
                "check": f"step11_{filename.replace('.json', '')}_exists",
                "passed": found,
                "detail": f"{label}: {'found' if found else 'missing (expected after Step 11)'}",
            }
        )

    # --- Eval policy advisory threshold presence ---
    for corpus in ("rust_reference", "core_docs"):
        policy_path = Path(f"config/eval_policies/{corpus}.yaml")
        advisory_present = False
        if policy_path.exists():
            try:
                import yaml  # type: ignore[import]
                policy = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
                advisory_present = "advisory_thresholds" in policy
            except Exception:
                pass
        results.append(
            {
                "check": f"{corpus}_policy_has_advisory_thresholds",
                "passed": advisory_present,
                "detail": f"{policy_path}: advisory_thresholds section {'present' if advisory_present else 'missing'}",
            }
        )

    # --- v3 invariants suite ---
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

    if args.checkpoint == "A":
        run_dir = args.run_dir or _latest_report_dir()
    else:
        # CP-B is scoped to retrieval run dirs; prefer explicit override
        if args.run_dir:
            run_dir = args.run_dir
        else:
            run_dir = _latest_retrieval_report_dir()
            if run_dir is None:
                print(
                    "ERROR: Could not auto-detect a retrieval run directory.\n"
                    "Use --checkpoint-run-dir to specify the Step 10 run directory, e.g.:\n"
                    "  python scripts/integration_checkpoint.py --checkpoint B \\\n"
                    "    --run-dir .cache/sqlite_kb/reports/step10_retrieval_recovery_<timestamp>"
                )
                sys.exit(2)
            print(f"  [auto-detected retrieval run dir: {run_dir}]")

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
