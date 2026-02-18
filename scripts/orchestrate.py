#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

from _common import (
    EXIT_POLICY_FAIL,
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    find_registry_baseline,
    generate_run_id,
    read_json,
    read_yaml,
    repo_root,
    resolve_extractor_paths,
    run_command,
    stable_scope_fingerprint,
    utc_now,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run seed->normalize->diff orchestration")
    parser.add_argument("--mode", choices=["change", "growth"], required=True)
    parser.add_argument("--corpus-pack", required=True)
    parser.add_argument("--base-run", help="Explicit baseline run id")
    parser.add_argument("--profile", choices=["quick", "full"], default="quick")
    parser.add_argument("--emit-cache-root", type=Path, default=Path(".cache/ops"))
    parser.add_argument("--allow-bootstrap", action="store_true")
    parser.add_argument("--allow-missing-traceability", action="store_true")
    return parser.parse_args()


def run_python_step(
    root: Path,
    command: list[str],
    report_path: Path | None = None,
) -> tuple[int, str]:
    completed = run_command([sys.executable, *command], cwd=root)
    if report_path is not None:
        payload = {
            "command": " ".join([sys.executable, *command]),
            "return_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        write_json(report_path, payload)
    return completed.returncode, completed.stdout + completed.stderr


def load_guideline_count(root: Path) -> int:
    path = root / "data" / "todo_guidelines.yaml"
    if not path.exists():
        return 0
    payload = read_yaml(path) or {}
    return len(payload.get("guidelines", []))


def load_seed_count(root: Path) -> int:
    path = root / "data" / "seed_topics.yaml"
    if not path.exists():
        return 0
    payload = read_yaml(path) or {}
    return len(payload.get("seed_topics", []))


def load_coverage_row_count(root: Path) -> int:
    path = root / "data" / "coverage_matrix.csv"
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def compare_with_baseline(
    mode: str,
    baseline_metrics: dict[str, Any],
    current_metrics: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[list[str], dict[str, float]]:
    change_policy = policy["change_mode"]
    growth_policy = policy["growth_mode"]

    deltas = {
        "seed_count_delta": float(
            current_metrics["seed_topic_count"] - baseline_metrics["seed_topic_count"]
        ),
        "coverage_row_delta": float(
            current_metrics["coverage_row_count"] - baseline_metrics["coverage_row_count"]
        ),
        "guideline_count_delta": float(
            current_metrics["guideline_count"] - baseline_metrics["guideline_count"]
        ),
        "traceability_score_delta": float(
            current_metrics["traceability_score"] - baseline_metrics["traceability_score"]
        ),
        "evidence_link_score_delta": float(
            current_metrics["evidence_link_score"] - baseline_metrics["evidence_link_score"]
        ),
    }

    violations: list[str] = []

    coverage_drop = max(0.0, -deltas["coverage_row_delta"])
    traceability_drop = max(0.0, -deltas["traceability_score_delta"])
    evidence_drop = max(0.0, -deltas["evidence_link_score_delta"])

    if mode == "change":
        if coverage_drop > change_policy["max_coverage_drop"]:
            violations.append(
                "coverage rows regressed by "
                f"{coverage_drop} (> {change_policy['max_coverage_drop']})"
            )
        if traceability_drop > change_policy["max_traceability_drop"]:
            violations.append(
                "traceability score regressed by "
                f"{traceability_drop} (> {change_policy['max_traceability_drop']})"
            )
        if evidence_drop > change_policy["max_evidence_link_drop"]:
            violations.append(
                "evidence-link score regressed by "
                f"{evidence_drop} (> {change_policy['max_evidence_link_drop']})"
            )
    else:
        if growth_policy["require_old_scope_non_regression"]:
            if coverage_drop > 0:
                violations.append(
                    "growth mode old-scope non-regression failed: coverage rows dropped"
                )
            if traceability_drop > 0:
                violations.append(
                    "growth mode old-scope non-regression failed: traceability score dropped"
                )
            if evidence_drop > 0:
                violations.append(
                    "growth mode old-scope non-regression failed: evidence-link score dropped"
                )

        if deltas["coverage_row_delta"] < growth_policy["min_new_scope_coverage_delta"]:
            violations.append(
                "growth mode coverage delta below minimum: "
                f"{deltas['coverage_row_delta']} < {growth_policy['min_new_scope_coverage_delta']}"
            )
        if deltas["guideline_count_delta"] < growth_policy["min_new_guidelines_delta"]:
            violations.append(
                "growth mode guideline delta below minimum: "
                f"{deltas['guideline_count_delta']} < {growth_policy['min_new_guidelines_delta']}"
            )

    return violations, deltas


def write_diff_artifacts(
    diff_json_path: Path,
    diff_md_path: Path,
    baseline_run_id: str,
    current_run_id: str,
    baseline_metrics: dict[str, Any],
    current_metrics: dict[str, Any],
    deltas: dict[str, float],
    violations: list[str],
) -> None:
    diff_payload = {
        "baseline_run_id": baseline_run_id,
        "current_run_id": current_run_id,
        "baseline_metrics": baseline_metrics,
        "current_metrics": current_metrics,
        "deltas": deltas,
        "violations": violations,
    }
    write_json(diff_json_path, diff_payload)

    lines = [
        f"# Diff {baseline_run_id} -> {current_run_id}",
        "",
        "## Deltas",
        f"- seed_topic_count: {deltas['seed_count_delta']}",
        f"- coverage_row_count: {deltas['coverage_row_delta']}",
        f"- guideline_count: {deltas['guideline_count_delta']}",
        f"- traceability_score: {deltas['traceability_score_delta']}",
        f"- evidence_link_score: {deltas['evidence_link_score_delta']}",
        "",
        "## Violations",
    ]
    if violations:
        lines.extend(f"- {item}" for item in violations)
    else:
        lines.append("- none")

    diff_md_path.parent.mkdir(parents=True, exist_ok=True)
    diff_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    root = repo_root()
    emit_root = root / args.emit_cache_root
    run_id = generate_run_id(prefix=args.mode)
    run_dir = emit_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    paths = resolve_extractor_paths(root)
    resolved_paths_payload = {
        "repo_root": str(paths.repo_root),
        "cache_root": str(paths.cache_root),
        "manifest_dir": str(paths.manifest_dir),
    }
    write_json(run_dir / "resolved_paths.json", resolved_paths_payload)

    start_time = utc_now()
    step_results: dict[str, dict[str, Any]] = {}
    runtime_errors: list[str] = []
    policy_errors: list[str] = []

    health_output = run_dir / "check_extractor_health.report.json"
    health_command = [
        "scripts/check_extractor_health.py",
        "--json-output",
        str(health_output),
    ]
    if args.profile == "quick":
        health_command.append("--skip-validate")

    code, output = run_python_step(
        root,
        health_command,
        report_path=run_dir / "check_extractor_health.step.json",
    )
    step_results["check_extractor_health"] = {
        "return_code": code,
        "output": output,
    }
    if code != 0:
        runtime_errors.append("check_extractor_health failed")

    if not runtime_errors:
        code, output = run_python_step(
            root,
            [
                "scripts/query_seeds.py",
                "--run-id",
                run_id,
                "--corpus-pack",
                args.corpus_pack,
                "--profile",
                args.profile,
            ],
            report_path=run_dir / "query_seeds.step.json",
        )
        step_results["query_seeds"] = {
            "return_code": code,
            "output": output,
        }
        if code != 0:
            runtime_errors.append("query_seeds failed")

    if not runtime_errors:
        code, output = run_python_step(
            root,
            ["scripts/build_seed_topics.py", "--run-id", run_id],
            report_path=run_dir / "build_seed_topics.step.json",
        )
        step_results["build_seed_topics"] = {
            "return_code": code,
            "output": output,
        }
        if code != 0:
            runtime_errors.append("build_seed_topics failed")

    schema_report_path = run_dir / "validate_schemas.report.json"
    if not runtime_errors:
        code, output = run_python_step(
            root,
            ["scripts/validate_schemas.py", "--json-output", str(schema_report_path)],
            report_path=run_dir / "validate_schemas.step.json",
        )
        step_results["validate_schemas"] = {
            "return_code": code,
            "output": output,
        }
        if code == EXIT_RUNTIME_FAIL:
            runtime_errors.append("validate_schemas runtime failure")
        elif code == EXIT_POLICY_FAIL:
            policy_errors.append("validate_schemas policy failure")

    traceability_report_path = run_dir / "check_traceability.report.json"
    if not runtime_errors:
        traceability_command = [
            "scripts/check_traceability.py",
            "--json-output",
            str(traceability_report_path),
        ]
        if args.allow_missing_traceability:
            traceability_command.append("--allow-missing")
        code, output = run_python_step(
            root,
            traceability_command,
            report_path=run_dir / "check_traceability.step.json",
        )
        step_results["check_traceability"] = {
            "return_code": code,
            "output": output,
        }
        if code == EXIT_RUNTIME_FAIL:
            runtime_errors.append("check_traceability runtime failure")
        elif code == EXIT_POLICY_FAIL:
            policy_errors.append("check_traceability policy failure")

    licensing_report_path = run_dir / "check_licensing_guard.report.json"
    if not runtime_errors:
        code, output = run_python_step(
            root,
            ["scripts/check_licensing_guard.py", "--json-output", str(licensing_report_path)],
            report_path=run_dir / "check_licensing_guard.step.json",
        )
        step_results["check_licensing_guard"] = {
            "return_code": code,
            "output": output,
        }
        if code == EXIT_RUNTIME_FAIL:
            runtime_errors.append("check_licensing_guard runtime failure")
        elif code == EXIT_POLICY_FAIL:
            policy_errors.append("check_licensing_guard policy failure")

    current_metrics = {
        "seed_topic_count": load_seed_count(root),
        "coverage_row_count": load_coverage_row_count(root),
        "guideline_count": load_guideline_count(root),
        "traceability_score": 0.0,
        "evidence_link_score": 0.0,
    }

    if traceability_report_path.exists():
        traceability_report = read_json(traceability_report_path)
        current_metrics["traceability_score"] = 1.0 if traceability_report.get("ok") else 0.0
        has_evidence_errors = any(
            "evidence_path" in message for message in traceability_report.get("errors", [])
        )
        current_metrics["evidence_link_score"] = 0.0 if has_evidence_errors else 1.0

    scope_payload = read_yaml(root / "data" / "target_scope.yaml") or {}
    scope_fingerprint = stable_scope_fingerprint(scope_payload.get("in_scope_target_ids", []))
    current_metrics["scope_fingerprint"] = scope_fingerprint

    policy = read_yaml(root / "config" / "change_growth_policy.yaml")
    run_registry = read_yaml(root / "data" / "run_registry.yaml")
    baseline_entry = None
    if args.base_run:
        baseline_entry = {
            "accepted_run_id": args.base_run,
            "corpus_pack_id": args.corpus_pack,
            "mode": args.mode,
        }
    else:
        baseline_entry = find_registry_baseline(run_registry, args.corpus_pack, args.mode)

    deltas: dict[str, float] | None = None
    comparison_violations: list[str] = []

    if baseline_entry is not None:
        baseline_run_id = baseline_entry["accepted_run_id"]
        baseline_metrics_path = emit_root / "runs" / baseline_run_id / "metrics.json"
        if baseline_metrics_path.exists():
            baseline_metrics_loaded = read_json(baseline_metrics_path)
            comparison_violations, deltas = compare_with_baseline(
                args.mode,
                baseline_metrics_loaded,
                current_metrics,
                policy,
            )
            if comparison_violations:
                policy_errors.extend(comparison_violations)

            diff_json_path = emit_root / "diffs" / f"{baseline_run_id}__{run_id}.json"
            diff_md_path = emit_root / "diffs" / f"{baseline_run_id}__{run_id}.md"
            write_diff_artifacts(
                diff_json_path,
                diff_md_path,
                baseline_run_id,
                run_id,
                baseline_metrics_loaded,
                current_metrics,
                deltas or {},
                comparison_violations,
            )
        else:
            message = f"baseline metrics missing for run {baseline_run_id}"
            if args.allow_bootstrap:
                policy_errors.append(f"bootstrap-warning: {message}")
            else:
                policy_errors.append(message)
    elif not args.allow_bootstrap:
        policy_errors.append("no baseline run available; pass --base-run or use --allow-bootstrap")

    manifest_payload = {
        "version": 1,
        "run_id": run_id,
        "started_at": start_time,
        "completed_at": utc_now(),
        "mode": args.mode,
        "profile": args.profile,
        "corpus_pack": args.corpus_pack,
        "base_run": baseline_entry["accepted_run_id"] if baseline_entry else None,
        "scope_fingerprint": scope_fingerprint,
        "step_results": step_results,
        "runtime_errors": runtime_errors,
        "policy_errors": policy_errors,
    }

    metrics_payload = {
        **current_metrics,
        "base_run": baseline_entry["accepted_run_id"] if baseline_entry else None,
        "deltas": deltas,
        "comparison_violations": comparison_violations,
    }

    promotion_candidate = {
        "version": 1,
        "run_id": run_id,
        "candidate_files": [
            "data/seed_topics.yaml",
            "data/guideline_categories.yaml",
            "data/todo_guidelines.yaml",
            "data/coverage_matrix.csv",
        ],
        "requires_signoff": True,
    }

    summary_lines = [
        f"# Orchestration Summary: {run_id}",
        "",
        f"- mode: {args.mode}",
        f"- profile: {args.profile}",
        f"- corpus_pack: {args.corpus_pack}",
        f"- base_run: {baseline_entry['accepted_run_id'] if baseline_entry else 'none'}",
        f"- seed_topic_count: {current_metrics['seed_topic_count']}",
        f"- guideline_count: {current_metrics['guideline_count']}",
        f"- coverage_row_count: {current_metrics['coverage_row_count']}",
        f"- runtime_errors: {len(runtime_errors)}",
        f"- policy_errors: {len(policy_errors)}",
    ]

    write_json(run_dir / "run_manifest.json", manifest_payload)
    write_json(run_dir / "metrics.json", metrics_payload)
    write_json(run_dir / "promotion_candidate.json", promotion_candidate)
    (run_dir / "summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    if runtime_errors:
        print(f"[orchestrate] runtime failure, see {run_dir / 'summary.md'}")
        return EXIT_RUNTIME_FAIL
    if policy_errors and not all(error.startswith("bootstrap-warning:") for error in policy_errors):
        print(f"[orchestrate] policy failure, see {run_dir / 'summary.md'}")
        return EXIT_POLICY_FAIL

    print(f"[orchestrate] success run_id={run_id}")
    print(f"[orchestrate] artifacts -> {run_dir.relative_to(root)}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
