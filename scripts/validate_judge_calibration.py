"""Validate Step 4 standalone judges against exemplar and known-bad RST sets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

if __package__ in {None, ""}:  # pragma: no cover - direct script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.import_utils import GUIDELINES_REPO_ROOT
from scripts.judges_v2.stage_b import (
    STAGE_B_JUDGES,
    _compute_verdict,
    evaluate_judge,
    load_judge_contracts,
)


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


def _calibration_aggregate_pass(per_judge_decisions: dict[str, str]) -> bool:
    pass_count = sum(1 for value in per_judge_decisions.values() if value == "pass")
    return pass_count >= 2


def _load_threshold_policy(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "aggregate": {"precision_min": 0.75, "recall_min": 0.70},
            "per_judge": {"precision_min": 0.65, "recall_min": 0.65},
        }
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


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
    exemplars = _select_exemplars(exemplar_manifest_path)

    per_judge_counts = {name: _empty_counts() for name in STAGE_B_JUDGES}
    aggregate_counts = _empty_counts()
    exemplar_verdicts: list[dict[str, Any]] = []

    positives = [("positive", path) for path in exemplars]
    negatives = [("negative", path) for path in sorted(known_bad_dir.glob("*.rst"))]

    for label, path in positives + negatives:
        rst = path.read_text(encoding="utf-8")
        expected_pass = label == "positive"
        per_judge_decisions: dict[str, str] = {}
        for judge_name in STAGE_B_JUDGES:
            result = evaluate_judge(
                judge_name,
                rst,
                [],
                contracts,
                judge_mode=judge_mode,
                model=model,
            )
            decision = str(result.get("decision", "fail")).strip().lower()
            per_judge_decisions[judge_name] = decision
            actual_pass = decision == "pass"
            _update_counts(per_judge_counts[judge_name], expected_pass, actual_pass)
            exemplar_verdicts.append(
                {
                    "judge": judge_name,
                    "example": str(path),
                    "label": label,
                    "verdict": decision,
                    "reason": result.get("summary", ""),
                }
            )

        _ = _compute_verdict(per_judge_decisions)
        _update_counts(
            aggregate_counts,
            expected_pass,
            _calibration_aggregate_pass(per_judge_decisions),
        )

    per_judge_metrics = {
        judge_name: {
            **per_judge_counts[judge_name],
            **_metrics_from_counts(per_judge_counts[judge_name]),
        }
        for judge_name in STAGE_B_JUDGES
    }
    aggregate_metrics = {**aggregate_counts, **_metrics_from_counts(aggregate_counts)}

    threshold_policy = _load_threshold_policy(threshold_policy_path)
    aggregate_cfg = threshold_policy.get("aggregate", {})
    per_judge_cfg = threshold_policy.get("per_judge", {})
    per_judge_overrides = threshold_policy.get("per_judge_overrides", {})

    aggregate_precision_min = float(aggregate_cfg.get("precision_min", 0.75))
    aggregate_recall_min = float(aggregate_cfg.get("recall_min", 0.70))
    per_judge_precision_min = float(per_judge_cfg.get("precision_min", 0.65))
    per_judge_recall_min = float(per_judge_cfg.get("recall_min", 0.65))

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
        judge_cfg = (
            per_judge_overrides.get(judge_name, {}) if isinstance(per_judge_overrides, dict) else {}
        )
        judge_precision_min = float(judge_cfg.get("precision_min", per_judge_precision_min))
        judge_recall_min = float(judge_cfg.get("recall_min", per_judge_recall_min))
        if metric["precision"] < judge_precision_min:
            threshold_failures.append(
                f"{judge_name} precision {metric['precision']:.3f} < {judge_precision_min:.3f}"
            )
        if metric["recall"] < judge_recall_min:
            threshold_failures.append(
                f"{judge_name} recall {metric['recall']:.3f} < {judge_recall_min:.3f}"
            )

    exemplar_report = {
        "judge_mode": judge_mode,
        "model": model,
        "calibration_exemplars": [str(path) for path in exemplars],
        "known_bad_examples": [str(path) for _, path in negatives],
        "judges": STAGE_B_JUDGES,
        "total_judge_calls": len(exemplar_verdicts),
        "exemplar_verdicts": exemplar_verdicts,
        "metrics": {
            "aggregate": aggregate_metrics,
            "per_judge": per_judge_metrics,
        },
        "threshold_policy": {
            "aggregate": {
                "precision_min": aggregate_precision_min,
                "recall_min": aggregate_recall_min,
            },
            "per_judge": {
                "precision_min": per_judge_precision_min,
                "recall_min": per_judge_recall_min,
            },
            "per_judge_overrides": per_judge_overrides,
        },
        "threshold_failures": threshold_failures,
        "thresholds_met": not threshold_failures,
        "calibration_passed": not threshold_failures,
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
                "mechanical_failures": len(bad_rst_report["mechanical_failures"]),
                "content_failures": len(bad_rst_report["content_failures"]),
            },
            indent=2,
        )
    )
    return 0 if exemplar_report["thresholds_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
