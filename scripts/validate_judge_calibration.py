"""Validate standalone judges against labeled positive/negative RST sets."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml

if __package__ in {None, ""}:  # pragma: no cover - direct script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.import_utils import GUIDELINES_REPO_ROOT
from scripts.judges_v2.stage_b import STAGE_B_JUDGES, evaluate_judge, load_judge_contracts


def _append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _select_exemplars(manifest_path: Path) -> list[Path]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("exemplars", [])
    selected: list[Path] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rel_path = str(entry.get("path", "")).strip()
        if not rel_path:
            continue
        selected.append(GUIDELINES_REPO_ROOT / rel_path)
    return selected


def _empty_counts() -> dict[str, int]:
    return {"tp": 0, "fp": 0, "tn": 0, "fn": 0}


def _update_counts(counts: dict[str, int], expected_pass: bool, actual_pass: bool) -> None:
    if expected_pass and actual_pass:
        counts["tp"] += 1
    elif expected_pass and not actual_pass:
        counts["fn"] += 1
    elif not expected_pass and actual_pass:
        counts["fp"] += 1
    else:
        counts["tn"] += 1


def _metrics_from_counts(counts: dict[str, int]) -> dict[str, float]:
    tp = float(counts["tp"])
    fp = float(counts["fp"])
    fn = float(counts["fn"])
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def _is_invocation_failure(result: dict[str, Any], judge_mode: str) -> tuple[bool, str]:
    if judge_mode != "llm":
        return False, ""
    reason_codes = [str(item) for item in result.get("reason_codes", []) if str(item)]
    for code in reason_codes:
        if (
            "judge_transport_failure" in code
            or "judge_output_empty" in code
            or "judge_output_no_json_found" in code
            or "judge_output_json_parse_error" in code
        ):
            return True, code
    return False, ""


def _normalize_policy(policy: dict[str, Any]) -> dict[str, Any]:
    if "current_thresholds" in policy:
        current = policy.get("current_thresholds", {})
    else:
        current = {
            "aggregate": policy.get("aggregate", {}),
            "per_judge": policy.get("per_judge", {}),
        }

    if "target_thresholds" in policy:
        target = policy.get("target_thresholds", {})
    else:
        target = {
            "aggregate": {"precision_min": 0.75, "recall_min": 0.70},
            "per_judge": {"precision_min": 0.65, "recall_min": 0.65},
        }

    return {
        "current_thresholds": current,
        "target_thresholds": target,
        "ratchet_review_step": int(policy.get("ratchet_review_step", 9)),
        "min_samples_per_class_for_strict": int(policy.get("min_samples_per_class_for_strict", 15)),
        "notes": str(policy.get("notes", "")).strip(),
    }


def _load_threshold_policy(path: Path) -> dict[str, Any]:
    if not path.exists():
        fallback = {
            "current_thresholds": {
                "aggregate": {"precision_min": 0.60, "recall_min": 0.25},
                "per_judge": {"precision_min": 0.60, "recall_min": 0.25},
            },
            "target_thresholds": {
                "aggregate": {"precision_min": 0.75, "recall_min": 0.70},
                "per_judge": {"precision_min": 0.65, "recall_min": 0.65},
            },
            "ratchet_review_step": 9,
            "min_samples_per_class_for_strict": 15,
            "notes": "Fallback policy generated because threshold file is missing.",
        }
        return fallback
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"Invalid threshold policy format: {path}")
    return _normalize_policy(loaded)


def _run_bad_rst_calibration(
    contracts: dict[str, Any],
    bad_rst_dir: Path,
    good_rst_dir: Path,
    *,
    judge_mode: str,
    model: str | None,
) -> dict[str, Any]:
    bad_by_prompt = {
        path.stem.upper().replace("-", "_"): path for path in bad_rst_dir.glob("*.rst")
    }
    good_by_prompt = {
        path.stem.upper().replace("-", "_"): path for path in good_rst_dir.glob("*.rst")
    }

    bad_rows: list[dict[str, Any]] = []
    good_rows: list[dict[str, Any]] = []
    mechanical: list[dict[str, Any]] = []
    content: list[dict[str, Any]] = []

    shared_prompts = sorted(set(bad_by_prompt).intersection(good_by_prompt))
    for prompt_id in shared_prompts:
        bad_rst = bad_by_prompt[prompt_id].read_text(encoding="utf-8")
        good_rst = good_by_prompt[prompt_id].read_text(encoding="utf-8")
        bad_verdicts: dict[str, dict[str, Any]] = {}
        good_verdicts: dict[str, dict[str, Any]] = {}
        for judge_name in STAGE_B_JUDGES:
            bad_verdicts[judge_name] = evaluate_judge(
                judge_name,
                bad_rst,
                [],
                contracts,
                judge_mode=judge_mode,
                model=model,
            )
            good_verdicts[judge_name] = evaluate_judge(
                judge_name,
                good_rst,
                [],
                contracts,
                judge_mode=judge_mode,
                model=model,
            )

            bad_decision = str(bad_verdicts[judge_name].get("decision", "fail"))
            good_decision = str(good_verdicts[judge_name].get("decision", "fail"))
            if bad_decision == "fail" and good_decision == "pass":
                mechanical.append(
                    {
                        "prompt_id": prompt_id,
                        "judge": judge_name,
                        "reason": "Renderer-fixed output passes where known-bad output fails.",
                    }
                )
            elif bad_decision == "fail" and good_decision == "fail":
                content.append(
                    {
                        "prompt_id": prompt_id,
                        "judge": judge_name,
                        "bad_reason_codes": bad_verdicts[judge_name].get("reason_codes", []),
                        "good_reason_codes": good_verdicts[judge_name].get("reason_codes", []),
                    }
                )

        bad_rows.append({"prompt_id": prompt_id, "verdicts": bad_verdicts})
        good_rows.append({"prompt_id": prompt_id, "verdicts": good_verdicts})

    return {
        "bad_rst": bad_rows,
        "good_rst": good_rows,
        "mechanical_failures": mechanical,
        "content_failures": content,
    }


def run_calibration(
    run_dir: Path,
    contracts_path: Path,
    exemplar_manifest_path: Path,
    known_bad_dir: Path,
    threshold_policy_path: Path,
    *,
    judge_mode: str,
    model: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    contracts = load_judge_contracts(contracts_path)
    threshold_policy = _load_threshold_policy(threshold_policy_path)

    progress_path = run_dir / "judge_calibration_progress.jsonl"
    if progress_path.exists():
        progress_path.unlink()

    exemplars = _select_exemplars(exemplar_manifest_path)
    negatives = sorted(known_bad_dir.glob("*.rst"))
    positives = [("positive", path) for path in exemplars]
    negatives_labeled = [("negative", path) for path in negatives]
    all_items = positives + negatives_labeled

    per_judge_counts = {name: _empty_counts() for name in STAGE_B_JUDGES}
    aggregate_counts = _empty_counts()
    exemplar_verdicts: list[dict[str, Any]] = []
    invocation_failures: list[dict[str, Any]] = []
    skipped_samples: list[dict[str, Any]] = []

    expected_total_calls = len(all_items) * len(STAGE_B_JUDGES)
    global_call_index = 0

    _append_jsonl(
        progress_path,
        {
            "type": "run_start",
            "timestamp": time.time(),
            "total_items": len(all_items),
            "expected_total_calls": expected_total_calls,
            "judge_mode": judge_mode,
        },
    )

    for item_index, (label, path) in enumerate(all_items, start=1):
        rst = path.read_text(encoding="utf-8")
        expected_pass = label == "positive"
        per_judge_decisions: dict[str, str] = {}
        sample_failures: list[dict[str, Any]] = []

        for judge_index, judge_name in enumerate(STAGE_B_JUDGES, start=1):
            global_call_index += 1
            phase = "exemplar" if label == "positive" else "negative"
            print(
                f"calibration: call {global_call_index}/{expected_total_calls} ({judge_name} on {path.stem})",
                file=sys.stderr,
                flush=True,
            )
            _append_jsonl(
                progress_path,
                {
                    "type": "judge_call_start",
                    "timestamp": time.time(),
                    "phase": phase,
                    "item_index": item_index,
                    "item_total": len(all_items),
                    "judge_index": judge_index,
                    "judge_total": len(STAGE_B_JUDGES),
                    "global_call_index": global_call_index,
                    "expected_total_calls": expected_total_calls,
                    "judge": judge_name,
                    "item": str(path),
                },
            )
            call_start = time.time()
            try:
                result = evaluate_judge(
                    judge_name,
                    rst,
                    [],
                    contracts,
                    judge_mode=judge_mode,
                    model=model,
                )
            except Exception as exc:  # pragma: no cover - defensive
                failure = {
                    "label": label,
                    "example": str(path),
                    "judge": judge_name,
                    "error": f"judge_invocation_exception:{type(exc).__name__}",
                }
                sample_failures.append(failure)
                invocation_failures.append(failure)
                _append_jsonl(
                    progress_path,
                    {
                        "type": "judge_call_end",
                        "timestamp": time.time(),
                        "global_call_index": global_call_index,
                        "judge": judge_name,
                        "item": str(path),
                        "ok": False,
                        "duration_ms": int((time.time() - call_start) * 1000),
                        "error": failure["error"],
                    },
                )
                continue

            decision = str(result.get("decision", "fail")).strip().lower()
            per_judge_decisions[judge_name] = decision
            reason_codes = [str(item) for item in result.get("reason_codes", []) if str(item)]
            invocation_failed, failure_reason = _is_invocation_failure(result, judge_mode)
            if invocation_failed:
                failure = {
                    "label": label,
                    "example": str(path),
                    "judge": judge_name,
                    "error": failure_reason,
                }
                sample_failures.append(failure)
                invocation_failures.append(failure)

            exemplar_verdicts.append(
                {
                    "judge": judge_name,
                    "example": str(path),
                    "label": label,
                    "verdict": decision,
                    "reason": result.get("summary", ""),
                }
            )
            _append_jsonl(
                progress_path,
                {
                    "type": "judge_call_end",
                    "timestamp": time.time(),
                    "global_call_index": global_call_index,
                    "judge": judge_name,
                    "item": str(path),
                    "ok": not invocation_failed,
                    "duration_ms": int((time.time() - call_start) * 1000),
                    "decision": decision,
                    "reason_codes": reason_codes,
                },
            )

        if sample_failures:
            skipped_samples.append(
                {
                    "label": label,
                    "example": str(path),
                    "reason": "invocation_failure",
                    "failures": sample_failures,
                }
            )
            continue

        for judge_name in STAGE_B_JUDGES:
            actual_pass = per_judge_decisions.get(judge_name, "fail") == "pass"
            _update_counts(per_judge_counts[judge_name], expected_pass, actual_pass)

        aggregate_pass = sum(1 for value in per_judge_decisions.values() if value == "pass") >= 2
        _update_counts(aggregate_counts, expected_pass, aggregate_pass)

    per_judge_metrics = {
        judge_name: {
            **per_judge_counts[judge_name],
            **_metrics_from_counts(per_judge_counts[judge_name]),
        }
        for judge_name in STAGE_B_JUDGES
    }
    aggregate_metrics = {**aggregate_counts, **_metrics_from_counts(aggregate_counts)}

    current_thresholds = threshold_policy["current_thresholds"]
    target_thresholds = threshold_policy["target_thresholds"]
    min_samples = int(threshold_policy["min_samples_per_class_for_strict"])

    aggregate_cfg = current_thresholds.get("aggregate", {})
    per_judge_cfg = current_thresholds.get("per_judge", {})

    aggregate_precision_min = float(aggregate_cfg.get("precision_min", 0.60))
    aggregate_recall_min = float(aggregate_cfg.get("recall_min", 0.25))
    per_judge_precision_min = float(per_judge_cfg.get("precision_min", 0.60))
    per_judge_recall_min = float(per_judge_cfg.get("recall_min", 0.25))

    baseline_counts = {"positive_n": len(positives), "negative_n": len(negatives_labeled)}
    skipped_positive = sum(1 for row in skipped_samples if row["label"] == "positive")
    skipped_negative = sum(1 for row in skipped_samples if row["label"] == "negative")
    sample_counts = {
        "positive_n": baseline_counts["positive_n"] - skipped_positive,
        "negative_n": baseline_counts["negative_n"] - skipped_negative,
    }

    warnings: list[str] = []
    confidence_mode = "normal"
    if sample_counts["positive_n"] < min_samples or sample_counts["negative_n"] < min_samples:
        confidence_mode = "low"
        warnings.append(
            "low_sample_confidence: sample counts below strict minimum "
            f"(positive_n={sample_counts['positive_n']}, negative_n={sample_counts['negative_n']}, "
            f"min={min_samples})"
        )
    if skipped_samples:
        warnings.append(
            f"degraded_samples_excluded: {len(skipped_samples)} sample(s) excluded due to invocation failures"
        )

    threshold_failures: list[str] = []
    if aggregate_metrics["precision"] < aggregate_precision_min:
        threshold_failures.append(
            f"aggregate precision {aggregate_metrics['precision']:.3f} < {aggregate_precision_min:.3f}"
        )
    if aggregate_metrics["recall"] < aggregate_recall_min:
        threshold_failures.append(
            f"aggregate recall {aggregate_metrics['recall']:.3f} < {aggregate_recall_min:.3f}"
        )

    for judge_name, metric in per_judge_metrics.items():
        if metric["precision"] < per_judge_precision_min:
            threshold_failures.append(
                f"{judge_name} precision {metric['precision']:.3f} < {per_judge_precision_min:.3f}"
            )
        if metric["recall"] < per_judge_recall_min:
            threshold_failures.append(
                f"{judge_name} recall {metric['recall']:.3f} < {per_judge_recall_min:.3f}"
            )

    degraded_below_min = (
        baseline_counts["positive_n"] >= min_samples and sample_counts["positive_n"] < min_samples
    ) or (
        baseline_counts["negative_n"] >= min_samples and sample_counts["negative_n"] < min_samples
    )

    hard_failure_reasons: list[str] = []
    if degraded_below_min:
        hard_failure_reasons.append("degradation_dropped_class_below_min_samples")
    if threshold_failures:
        hard_failure_reasons.extend(threshold_failures)

    exemplar_report = {
        "judge_mode": judge_mode,
        "model": model,
        "calibration_exemplars": [str(path) for path in exemplars],
        "known_bad_examples": [str(path) for _, path in negatives_labeled],
        "judges": STAGE_B_JUDGES,
        "total_judge_calls": len(exemplar_verdicts),
        "exemplar_verdicts": exemplar_verdicts,
        "invocation_failures": invocation_failures,
        "skipped_samples": skipped_samples,
        "sample_counts": sample_counts,
        "baseline_sample_counts": baseline_counts,
        "confidence_mode": confidence_mode,
        "warnings": warnings,
        "metrics": {
            "aggregate": aggregate_metrics,
            "per_judge": per_judge_metrics,
        },
        "threshold_policy": {
            "current_thresholds": current_thresholds,
            "target_thresholds": target_thresholds,
            "ratchet_review_step": threshold_policy["ratchet_review_step"],
            "min_samples_per_class_for_strict": min_samples,
            "notes": threshold_policy.get("notes", ""),
        },
        "threshold_failures": threshold_failures,
        "hard_failure_reasons": hard_failure_reasons,
        "thresholds_met": not threshold_failures,
        "calibration_passed": not hard_failure_reasons,
    }

    bad_rst_report = _run_bad_rst_calibration(
        contracts=contracts,
        bad_rst_dir=known_bad_dir,
        good_rst_dir=run_dir / "rerendered_rst",
        judge_mode=judge_mode,
        model=model,
    )

    (run_dir / "judge_calibration_report.json").write_text(
        json.dumps(exemplar_report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "judge_calibration_bad_rst_results.json").write_text(
        json.dumps(bad_rst_report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    summary = {
        "status": "pass" if exemplar_report["calibration_passed"] else "fail",
        "total_calls_attempted": expected_total_calls,
        "total_calls_recorded": len(exemplar_verdicts) + len(invocation_failures),
        "calls_succeeded": expected_total_calls - len(invocation_failures),
        "calls_failed": len(invocation_failures),
        "last_in_flight": invocation_failures[-1] if invocation_failures else None,
    }
    (run_dir / "judge_calibration_progress_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _append_jsonl(
        progress_path,
        {
            "type": "run_end",
            "timestamp": time.time(),
            "status": summary["status"],
            "calls_failed": summary["calls_failed"],
        },
    )

    return exemplar_report, bad_rst_report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate standalone judge calibration")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument(
        "--judge-contracts",
        default=Path("config/s0/judge_prompt_contracts.yaml"),
        type=Path,
    )
    parser.add_argument(
        "--exemplar-manifest",
        default=Path("data/exemplar_manifest.json"),
        type=Path,
    )
    parser.add_argument(
        "--known-bad-dir",
        default=Path(".cache/sqlite_kb/reports/phase_a_opencode_v3_exec2/generated_guidelines_rst"),
        type=Path,
    )
    parser.add_argument(
        "--threshold-policy",
        default=Path("config/s0/judge_calibration_thresholds.yaml"),
        type=Path,
    )
    parser.add_argument("--judge-mode", choices=["llm", "heuristic"], default="llm")
    parser.add_argument("--model", default="", type=str)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    exemplar_report, bad_rst_report = run_calibration(
        run_dir=args.run_dir.expanduser().resolve(),
        contracts_path=args.judge_contracts.expanduser().resolve(),
        exemplar_manifest_path=args.exemplar_manifest.expanduser().resolve(),
        known_bad_dir=args.known_bad_dir.expanduser().resolve(),
        threshold_policy_path=args.threshold_policy.expanduser().resolve(),
        judge_mode=args.judge_mode,
        model=args.model or None,
    )
    print(
        json.dumps(
            {
                "calibration_passed": exemplar_report["calibration_passed"],
                "thresholds_met": exemplar_report["thresholds_met"],
                "confidence_mode": exemplar_report["confidence_mode"],
                "mechanical_failures": len(bad_rst_report["mechanical_failures"]),
                "content_failures": len(bad_rst_report["content_failures"]),
            },
            indent=2,
        )
    )
    return 0 if exemplar_report["calibration_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
