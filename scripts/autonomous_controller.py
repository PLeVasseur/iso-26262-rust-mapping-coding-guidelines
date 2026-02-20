#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from _common import (
    EXIT_POLICY_FAIL,
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    find_registry_baseline,
    read_json,
    read_yaml,
    repo_root,
    run_command,
    stable_scope_fingerprint,
    utc_now,
    write_json,
    write_yaml,
)
from controller_actions import apply_candidate, generate_candidates
from controller_decision import build_decision_packet, resolve_candidate_selection
from controller_observe import observe_repo
from controller_scoring import (
    evaluation_sort_key,
    improves,
    metric_vector,
    regression_flags,
    weighted_score,
)

MUTABLE_PATHS = [
    "data/coverage_matrix.csv",
    "data/decomposition_report.yaml",
    "data/guideline_categories.yaml",
    "data/target_scope.yaml",
    "data/todo_guidelines.yaml",
    "tests/guidelines",
]

COMMIT_PATHS = [
    "data/todo_guidelines.yaml",
    "data/coverage_matrix.csv",
    "data/guideline_categories.yaml",
    "data/target_scope.yaml",
    "data/decomposition_report.yaml",
    "tests/guidelines",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run autonomous convergence controller")
    parser.add_argument("--session-id", default="autonomous")
    parser.add_argument("--resume-session")
    parser.add_argument("--mode", choices=["change", "growth"], default="growth")
    parser.add_argument("--profile", choices=["quick", "full"], default="quick")
    parser.add_argument("--max-iterations", type=int, default=30)
    parser.add_argument("--stall-window", type=int, default=5)
    parser.add_argument("--beam-width", type=int, default=4)
    parser.add_argument("--max-actions-per-bundle", type=int, default=3)
    parser.add_argument("--full-eval-every", type=int, default=3)
    parser.add_argument("--full-eval-top-k", type=int, default=2)
    parser.add_argument("--success-window", type=int, default=3)
    parser.add_argument("--suppress-after-failures", type=int, default=2)
    parser.set_defaults(commit_on_accept=False, strict_promotion=True)
    parser.add_argument("--commit-each-accept", dest="commit_on_accept", action="store_true")
    parser.add_argument("--no-commit-on-accept", dest="commit_on_accept", action="store_false")
    parser.add_argument("--strict-promotion", dest="strict_promotion", action="store_true")
    parser.add_argument("--no-strict-promotion", dest="strict_promotion", action="store_false")
    parser.add_argument(
        "--allow-main-branch-commits",
        action="store_true",
        help="Allow commit-on-accept while on main/master",
    )
    parser.add_argument("--single-iteration", action="store_true")
    parser.add_argument(
        "--decision-policy",
        type=Path,
        default=Path("config/controller_decision_policy.yaml"),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--use-orchestrate", action="store_true")
    parser.add_argument("--allow-bootstrap", action="store_true")
    parser.add_argument("--corpus-pack")
    args = parser.parse_args()
    if args.resume_session and str(args.resume_session).strip():
        args.session_id = str(args.resume_session).strip()
    return args


def controller_paths(root: Path, session_id: str) -> dict[str, Path]:
    session_root = root / ".cache" / "controller" / session_id
    return {
        "session_root": session_root,
        "iterations_root": session_root / "iterations",
        "handoff_root": session_root / "handoff",
        "state_json": session_root / "state.json",
        "dashboard_md": session_root / "dashboard.md",
        "final_report_md": session_root / "final_report.md",
        "blocker_json": session_root / "blocker_report.json",
    }


def _default_state(args: argparse.Namespace, corpus_pack: str) -> dict[str, Any]:
    return {
        "version": 1,
        "session_id": args.session_id,
        "status": "running",
        "iteration": 0,
        "max_iterations": args.max_iterations,
        "stall_window": args.stall_window,
        "consecutive_successes": 0,
        "no_improvement_count": 0,
        "best_observation": {},
        "last_observation": {},
        "history": [],
        "bundle_failure_counts": {},
        "candidate_signatures": [],
        "last_orchestrate_run_id": "",
        "last_iteration_decision": "",
        "last_selected_by": "none",
        "last_selection_reason": "",
        "last_commit_sha": "",
        "decision_fallback_count": 0,
        "corpus_pack": corpus_pack,
        "handoff_recommendation": "blocked",
        "started_at": utc_now(),
        "updated_at": utc_now(),
    }


def load_or_init_state(
    paths: dict[str, Path], args: argparse.Namespace, corpus_pack: str
) -> dict[str, Any]:
    state_path = paths["state_json"]
    if state_path.exists():
        with state_path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
        defaults = _default_state(args, corpus_pack)
        for key, value in defaults.items():
            if key not in state:
                state[key] = value
        state["corpus_pack"] = str(state.get("corpus_pack") or corpus_pack)
        return state

    return _default_state(args, corpus_pack)


def save_state(paths: dict[str, Path], state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    write_json(paths["state_json"], state)


def append_history(
    state: dict[str, Any],
    iteration: int,
    decision: str,
    note: str,
    candidate_id: str,
    selected_by: str = "none",
    selection_reason: str = "",
    commit_sha: str = "",
) -> None:
    payload = {
        "iteration": iteration,
        "decision": decision,
        "candidate_id": candidate_id,
        "recorded_at": utc_now(),
        "note": note,
        "selected_by": selected_by,
        "selection_reason": selection_reason,
    }
    if commit_sha:
        payload["commit_sha"] = commit_sha
    state["history"].append(payload)


def write_dashboard(
    paths: dict[str, Path], state: dict[str, Any], observation: dict[str, Any]
) -> None:
    lines = [
        f"# Controller Dashboard: {state['session_id']}",
        "",
        f"- status: {state['status']}",
        f"- iteration: {state['iteration']}/{state['max_iterations']}",
        f"- no_improvement_count: {state['no_improvement_count']}",
        f"- consecutive_successes: {state['consecutive_successes']}",
        f"- last_orchestrate_run_id: {state.get('last_orchestrate_run_id', '')}",
        f"- iso_obligation_coverage: {observation.get('iso_obligation_coverage', 0.0)}",
        f"- target_fanout_gap_count: {observation.get('target_fanout_gap_count', 0)}",
        f"- fls_span_gap_count: {observation.get('fls_span_gap_count', 0)}",
        f"- quality_gap_count: {observation.get('quality_gap_count', 0)}",
        f"- placeholder_gap_count: {observation.get('placeholder_gap_count', 0)}",
        f"- known_good_alignment_gap_count: {observation.get('known_good_alignment_gap_count', 0)}",
        f"- known_good_alignment_average: {observation.get('known_good_alignment_average', 0.0)}",
        f"- example_outcome_gap_count: {observation.get('example_outcome_gap_count', 0)}",
        f"- example_assertion_gap_count: {observation.get('example_assertion_gap_count', 0)}",
        f"- example_negative_evidence_gap_count: {observation.get('example_negative_evidence_gap_count', 0)}",
        f"- example_diversity_gap_count: {observation.get('example_diversity_gap_count', 0)}",
        f"- example_outcome_match_ratio: {observation.get('example_outcome_match_ratio', 0.0)}",
        f"- example_assertion_backed_ratio: {observation.get('example_assertion_backed_ratio', 0.0)}",
        f"- example_negative_evidence_strength_ratio: {observation.get('example_negative_evidence_strength_ratio', 0.0)}",
        f"- example_documented_only_ratio: {observation.get('example_documented_only_ratio', 0.0)}",
        f"- example_unique_signature_ratio: {observation.get('example_unique_signature_ratio', 0.0)}",
        f"- duplication_gap_count: {observation.get('duplication_gap_count', 0)}",
        f"- duplication_exception_missing_count: {observation.get('duplication_exception_missing_count', 0)}",
        f"- rust_signal_gap_count: {observation.get('rust_signal_gap_count', 0)}",
        f"- rust_signal_coverage: {observation.get('rust_signal_coverage', 0.0)}",
        f"- diversity_unique_token_ratio: {observation.get('diversity_unique_token_ratio', 0.0)}",
        f"- total_deficit_count: {observation.get('total_deficit_count', 0)}",
        f"- weighted_score: {weighted_score(observation)}",
    ]
    paths["dashboard_md"].parent.mkdir(parents=True, exist_ok=True)
    paths["dashboard_md"].write_text("\n".join(lines) + "\n", encoding="utf-8")


def success_condition(observation: dict[str, Any]) -> bool:
    lane_keys = [
        "hard_gate_pass",
        "iso_lane_pass",
        "decomposition_lane_pass",
        "fls_lane_pass",
        "quality_lane_pass",
    ]
    return all(bool(observation.get(key, False)) for key in lane_keys)


QUALITY_PROGRESS_DEFICIT_TYPES = {
    "quality_gap",
    "placeholder_gap",
    "example_gap",
    "example_outcome_gap",
    "example_assertion_gap",
    "example_negative_evidence_gap",
    "example_diversity_gap",
}


def touched_guideline_ids(candidate: dict[str, Any]) -> set[str]:
    touched: set[str] = set()
    for action in candidate.get("actions") or []:
        guideline_id = str((action or {}).get("guideline_id") or "").strip()
        if guideline_id:
            touched.add(guideline_id)
    return touched


def quality_deficit_count(observation: dict[str, Any], guideline_id: str) -> int:
    deficits = observation.get("deficits") or []
    return sum(
        1
        for deficit in deficits
        if str(deficit.get("guideline_id") or "").strip() == guideline_id
        and str(deficit.get("type") or "") in QUALITY_PROGRESS_DEFICIT_TYPES
    )


def stagnant_quality_guidelines(
    before_observation: dict[str, Any],
    after_observation: dict[str, Any],
    guideline_ids: set[str],
) -> list[str]:
    stagnant: list[str] = []
    for guideline_id in sorted(guideline_ids):
        before_count = quality_deficit_count(before_observation, guideline_id)
        if before_count <= 0:
            continue
        after_count = quality_deficit_count(after_observation, guideline_id)
        if after_count >= before_count:
            stagnant.append(guideline_id)
    return stagnant


def snapshot_workspace(root: Path, backup_root: Path) -> None:
    for rel_path in MUTABLE_PATHS:
        source = root / rel_path
        destination = backup_root / rel_path
        if not source.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)


def restore_workspace(root: Path, backup_root: Path) -> None:
    for rel_path in MUTABLE_PATHS:
        current = root / rel_path
        backup = backup_root / rel_path

        if current.exists():
            if current.is_dir():
                shutil.rmtree(current)
            else:
                current.unlink()

        if backup.exists():
            current.parent.mkdir(parents=True, exist_ok=True)
            if backup.is_dir():
                shutil.copytree(backup, current, dirs_exist_ok=True)
            else:
                shutil.copy2(backup, current)


def parse_run_id_from_output(output: str) -> str:
    match = re.search(r"run_id=([A-Za-z0-9_-]+)", output)
    if match:
        return match.group(1)
    return ""


def run_orchestrate(
    root: Path,
    args: argparse.Namespace,
    corpus_pack: str,
    profile: str,
    output_path: Path,
) -> tuple[bool, dict[str, Any]]:
    command = [
        sys.executable,
        "scripts/orchestrate.py",
        "--mode",
        args.mode,
        "--profile",
        profile,
        "--corpus-pack",
        corpus_pack,
    ]
    if args.allow_bootstrap:
        command.append("--allow-bootstrap")

    completed = run_command(command, cwd=root)
    combined_output = f"{completed.stdout}\n{completed.stderr}"
    run_id = parse_run_id_from_output(combined_output)

    report = {
        "command": " ".join(command),
        "return_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "run_id": run_id,
        "profile": profile,
    }
    write_json(output_path, report)
    return completed.returncode == 0, report


def evaluate_candidate(
    root: Path,
    args: argparse.Namespace,
    corpus_pack: str,
    iteration_dir: Path,
    before_observation: dict[str, Any],
    candidate: dict[str, Any],
    evaluation_profile: str,
    known_good_alignment_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_id = str(candidate.get("candidate_id") or "unknown")
    candidate_dir = iteration_dir / f"candidate_{candidate_id}_{evaluation_profile}"
    candidate_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="controller-candidate-") as temp_dir:
        backup_root = Path(temp_dir)
        snapshot_workspace(root, backup_root)

        apply_result = apply_candidate(root, candidate)
        write_json(candidate_dir / "apply_result.json", apply_result)
        if not apply_result.get("ok", False):
            restore_workspace(root, backup_root)
            return {
                "candidate_id": candidate_id,
                "bundle_signature": str(candidate.get("bundle_signature") or ""),
                "accepted": False,
                "observation": before_observation,
                "regressions": ["apply_candidate_failed"],
                "note": "candidate apply or regeneration failed",
                "weighted_score": weighted_score(before_observation),
                "metric_vector": metric_vector(before_observation),
                "evaluation_profile": evaluation_profile,
                "mutation_footprint_estimate": int(
                    candidate.get("mutation_footprint_estimate") or 0
                ),
            }

        orchestrate_ok = True
        orchestrate_report: dict[str, Any] = {"enabled": False}
        if args.use_orchestrate:
            orchestrate_ok, orchestrate_report = run_orchestrate(
                root,
                args,
                corpus_pack,
                evaluation_profile,
                candidate_dir / "orchestrate.json",
            )

        after_observation = observe_repo(
            root,
            candidate_dir / "observation",
            known_good_alignment_overrides=known_good_alignment_overrides,
        )
        regressions = regression_flags(before_observation, after_observation)
        improved = improves(before_observation, after_observation)
        accepted = orchestrate_ok and improved and not regressions

        evaluation = {
            "candidate_id": candidate_id,
            "bundle_signature": str(candidate.get("bundle_signature") or ""),
            "accepted": accepted,
            "observation": after_observation,
            "regressions": regressions,
            "note": "improved" if accepted else "not improved or regressed",
            "weighted_score": weighted_score(after_observation),
            "metric_vector": metric_vector(after_observation),
            "evaluation_profile": evaluation_profile,
            "mutation_footprint_estimate": int(candidate.get("mutation_footprint_estimate") or 0),
            "orchestrate_run_id": str(orchestrate_report.get("run_id") or ""),
        }

        write_json(candidate_dir / "evaluation.json", evaluation)
        restore_workspace(root, backup_root)
        return evaluation


def _is_allowed_commit_path(path: str) -> bool:
    cleaned = path.strip()
    for allowed in COMMIT_PATHS:
        if cleaned == allowed:
            return True
        if cleaned.startswith(f"{allowed}/"):
            return True
    return False


def _staged_paths(root: Path) -> tuple[bool, list[str], str]:
    completed = run_command(["git", "diff", "--cached", "--name-only"], cwd=root)
    if completed.returncode != 0:
        return False, [], completed.stderr or completed.stdout
    values = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return True, values, ""


def commit_iteration(
    root: Path,
    session_id: str,
    iteration: int,
    candidate_id: str,
    selected_by: str,
    selected_profile: str,
    run_id: str,
    allow_main_branch_commits: bool,
) -> dict[str, Any]:
    branch_result = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    if branch_result.returncode != 0:
        return {
            "ok": False,
            "committed": False,
            "commit_sha": "",
            "message": "failed determining git branch",
            "output": branch_result.stderr or branch_result.stdout,
        }

    branch_name = branch_result.stdout.strip()
    if not allow_main_branch_commits and branch_name in {"main", "master"}:
        return {
            "ok": False,
            "committed": False,
            "commit_sha": "",
            "message": "branch guard rejected commit on protected branch",
            "output": (
                f"current branch is `{branch_name}`; switch to a feature branch or "
                "pass --allow-main-branch-commits"
            ),
        }

    staged_ok, staged_before, staged_error = _staged_paths(root)
    if not staged_ok:
        return {
            "ok": False,
            "committed": False,
            "commit_sha": "",
            "message": "failed reading staged paths",
            "output": staged_error,
        }

    unexpected_before = [path for path in staged_before if not _is_allowed_commit_path(path)]
    if unexpected_before:
        return {
            "ok": False,
            "committed": False,
            "commit_sha": "",
            "message": "unexpected pre-staged paths",
            "output": ", ".join(sorted(unexpected_before)),
        }

    add_result = run_command(["git", "add", *COMMIT_PATHS], cwd=root)
    if add_result.returncode != 0:
        return {
            "ok": False,
            "committed": False,
            "commit_sha": "",
            "message": "git add failed",
            "output": add_result.stderr or add_result.stdout,
        }

    staged_ok, staged_after, staged_error = _staged_paths(root)
    if not staged_ok:
        return {
            "ok": False,
            "committed": False,
            "commit_sha": "",
            "message": "failed reading staged paths",
            "output": staged_error,
        }

    unexpected_after = [path for path in staged_after if not _is_allowed_commit_path(path)]
    if unexpected_after:
        return {
            "ok": False,
            "committed": False,
            "commit_sha": "",
            "message": "unexpected staged paths",
            "output": ", ".join(sorted(unexpected_after)),
        }

    if not staged_after:
        return {
            "ok": True,
            "committed": False,
            "commit_sha": "",
            "message": "no relevant changes to commit",
            "output": "",
        }

    subject = f"chore(controller-loop): accept {session_id} i{iteration:03d} {candidate_id}"
    body = "\n".join(
        [
            f"Controller-Session: {session_id}",
            f"Controller-Iteration: {iteration}",
            f"Controller-Candidate: {candidate_id}",
            f"Controller-Selected-By: {selected_by}",
            f"Controller-Profile: {selected_profile}",
            f"Controller-Run-Id: {run_id or 'none'}",
        ]
    )
    commit_result = run_command(["git", "commit", "-m", subject, "-m", body], cwd=root)
    if commit_result.returncode != 0:
        return {
            "ok": False,
            "committed": False,
            "commit_sha": "",
            "message": "git commit failed",
            "output": commit_result.stderr or commit_result.stdout,
        }

    sha_result = run_command(["git", "rev-parse", "HEAD"], cwd=root)
    if sha_result.returncode != 0:
        return {
            "ok": False,
            "committed": False,
            "commit_sha": "",
            "message": "failed resolving commit sha",
            "output": sha_result.stderr or sha_result.stdout,
        }

    return {
        "ok": True,
        "committed": True,
        "commit_sha": sha_result.stdout.strip(),
        "message": subject,
        "output": commit_result.stdout,
    }


def write_blocker_report(
    paths: dict[str, Path],
    state: dict[str, Any],
    reason: str,
    observation: dict[str, Any],
    attempted_candidates: list[dict[str, Any]],
) -> None:
    payload = {
        "version": 1,
        "session_id": state["session_id"],
        "status": state["status"],
        "reason": reason,
        "iteration": state["iteration"],
        "max_iterations": state["max_iterations"],
        "stall_window": state["stall_window"],
        "no_improvement_count": state["no_improvement_count"],
        "remaining_deficits": observation.get("deficits", []),
        "attempted_candidates": attempted_candidates,
        "generated_at": utc_now(),
    }
    write_json(paths["blocker_json"], payload)


def write_iteration_record(
    iteration_dir: Path,
    session_id: str,
    iteration: int,
    before_observation: dict[str, Any],
    candidates: list[dict[str, Any]],
    evaluations: list[dict[str, Any]],
    decision: str,
    selected_candidate_id: str,
    after_observation: dict[str, Any] | None = None,
    selection_source: str = "none",
    selection_reason: str = "",
    commit: dict[str, Any] | None = None,
) -> None:
    payload = {
        "version": 1,
        "session_id": session_id,
        "iteration": iteration,
        "before_observation": before_observation,
        "after_observation": after_observation or {},
        "candidates": candidates,
        "evaluations": evaluations,
        "decision": decision,
        "selected_candidate_id": selected_candidate_id,
        "selection_source": selection_source,
        "selection_reason": selection_reason,
        "recorded_at": utc_now(),
    }
    if commit:
        payload["commit"] = commit
    write_json(iteration_dir / "iteration.json", payload)


def validate_payload_with_schema(root: Path, schema_rel_path: str, payload: Any) -> list[str]:
    schema = read_json(root / schema_rel_path)
    validator = Draft202012Validator(schema)
    return [error.message for error in validator.iter_errors(payload)]


def lane_status_payload(observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "hard_gate_pass": bool(observation.get("hard_gate_pass", False)),
        "iso_lane_pass": bool(observation.get("iso_lane_pass", False)),
        "decomposition_lane_pass": bool(observation.get("decomposition_lane_pass", False)),
        "fls_lane_pass": bool(observation.get("fls_lane_pass", False)),
        "quality_lane_pass": bool(observation.get("quality_lane_pass", False)),
        "runtime_failures": int(observation.get("runtime_failures", 0)),
        "policy_failures": int(observation.get("policy_failures", 0)),
        "iso_obligation_coverage": float(observation.get("iso_obligation_coverage", 0.0)),
        "target_fanout_gap_count": int(observation.get("target_fanout_gap_count", 0)),
        "fls_span_gap_count": int(observation.get("fls_span_gap_count", 0)),
        "fls_chapter_gap_count": int(observation.get("fls_chapter_gap_count", 0)),
        "quality_gap_count": int(observation.get("quality_gap_count", 0)),
        "placeholder_gap_count": int(observation.get("placeholder_gap_count", 0)),
        "example_gap_count": int(observation.get("example_gap_count", 0)),
        "known_good_alignment_gap_count": int(observation.get("known_good_alignment_gap_count", 0)),
        "known_good_alignment_average": float(observation.get("known_good_alignment_average", 0.0)),
        "duplication_gap_count": int(observation.get("duplication_gap_count", 0)),
        "duplication_exception_missing_count": int(
            observation.get("duplication_exception_missing_count", 0)
        ),
        "rust_signal_gap_count": int(observation.get("rust_signal_gap_count", 0)),
        "rust_signal_coverage": float(observation.get("rust_signal_coverage", 0.0)),
        "diversity_unique_token_ratio": float(observation.get("diversity_unique_token_ratio", 0.0)),
    }


def recommend_handoff_status(
    lane_status: dict[str, Any],
    consecutive_successes: int,
    success_window: int,
    has_run_id: bool,
) -> tuple[str, str]:
    if not bool(lane_status.get("hard_gate_pass", False)):
        return "blocked", "hard gates failing"

    lane_keys = ["iso_lane_pass", "decomposition_lane_pass", "fls_lane_pass", "quality_lane_pass"]
    if not all(bool(lane_status.get(key, False)) for key in lane_keys):
        return "blocked", "one or more quality lanes failing"

    if consecutive_successes < success_window:
        return "needs_review", "success window not reached"

    if not has_run_id:
        return "needs_review", "no accepted orchestration run id available"

    return "ready", "all lanes pass and convergence window satisfied"


def delta_summary_payload(
    root: Path,
    corpus_pack: str,
    mode: str,
    current_run_id: str,
    observation: dict[str, Any],
) -> dict[str, Any]:
    run_registry = read_yaml(root / "data/run_registry.yaml") or {"accepted_runs": []}
    baseline_entry = find_registry_baseline(run_registry, corpus_pack, mode)
    baseline_run_id = str((baseline_entry or {}).get("accepted_run_id") or "")

    current_metrics = {
        "iso_obligation_coverage": float(observation.get("iso_obligation_coverage", 0.0)),
        "target_fanout_gap_count": int(observation.get("target_fanout_gap_count", 0)),
        "fls_span_gap_count": int(observation.get("fls_span_gap_count", 0)),
        "fls_chapter_coverage": float(observation.get("fls_chapter_coverage", 0.0)),
        "quality_gap_count": int(observation.get("quality_gap_count", 0)),
        "placeholder_gap_count": int(observation.get("placeholder_gap_count", 0)),
        "known_good_alignment_gap_count": int(observation.get("known_good_alignment_gap_count", 0)),
        "known_good_alignment_average": float(observation.get("known_good_alignment_average", 0.0)),
        "duplication_gap_count": int(observation.get("duplication_gap_count", 0)),
        "duplication_exception_missing_count": int(
            observation.get("duplication_exception_missing_count", 0)
        ),
        "rust_signal_gap_count": int(observation.get("rust_signal_gap_count", 0)),
        "rust_signal_coverage": float(observation.get("rust_signal_coverage", 0.0)),
        "diversity_unique_token_ratio": float(observation.get("diversity_unique_token_ratio", 0.0)),
    }

    baseline_metrics = {}
    deltas = {}
    if baseline_run_id:
        baseline_metrics_path = root / ".cache" / "ops" / "runs" / baseline_run_id / "metrics.json"
        if baseline_metrics_path.exists():
            baseline_metrics = read_json(baseline_metrics_path)
            for key, value in current_metrics.items():
                baseline_value = baseline_metrics.get(key)
                if isinstance(value, int):
                    deltas[key] = int(value) - int(baseline_value or 0)
                else:
                    deltas[key] = round(float(value) - float(baseline_value or 0.0), 6)

    return {
        "version": 1,
        "corpus_pack": corpus_pack,
        "mode": mode,
        "baseline_run_id": baseline_run_id,
        "current_run_id": current_run_id,
        "current_metrics": current_metrics,
        "baseline_metrics": baseline_metrics,
        "deltas": deltas,
        "generated_at": utc_now(),
    }


def load_scope_fingerprint(root: Path, run_id: str) -> str:
    if run_id:
        metrics_path = root / ".cache" / "ops" / "runs" / run_id / "metrics.json"
        if metrics_path.exists():
            metrics = read_json(metrics_path)
            value = str(metrics.get("scope_fingerprint") or "").strip()
            if value:
                return value

    scope_payload = read_yaml(root / "data/target_scope.yaml") or {}
    targets = scope_payload.get("in_scope_target_ids", [])
    return stable_scope_fingerprint([str(item) for item in targets])


def write_handoff_package(
    root: Path,
    paths: dict[str, Path],
    state: dict[str, Any],
    observation: dict[str, Any],
    args: argparse.Namespace,
    corpus_pack: str,
) -> tuple[str, list[str]]:
    handoff_root = paths["handoff_root"]
    handoff_root.mkdir(parents=True, exist_ok=True)

    run_id = str(state.get("last_orchestrate_run_id") or "")
    lane_status = lane_status_payload(observation)
    write_json(handoff_root / "lane_status.json", lane_status)

    delta_summary = delta_summary_payload(root, corpus_pack, args.mode, run_id, observation)
    write_json(handoff_root / "delta_summary.json", delta_summary)

    has_run_id = bool(run_id)
    recommendation, reason = recommend_handoff_status(
        lane_status,
        int(state.get("consecutive_successes", 0)),
        args.success_window,
        has_run_id,
    )

    handoff_payload = {
        "version": 1,
        "session_id": state["session_id"],
        "status": state["status"],
        "recommendation": recommendation,
        "reason": reason,
        "mode": args.mode,
        "corpus_pack": corpus_pack,
        "run_id": run_id,
        "consecutive_successes": int(state.get("consecutive_successes", 0)),
        "required_success_window": args.success_window,
        "last_iteration_decision": str(state.get("last_iteration_decision") or ""),
        "last_selected_by": str(state.get("last_selected_by") or "none"),
        "last_selection_reason": str(state.get("last_selection_reason") or ""),
        "last_commit_sha": str(state.get("last_commit_sha") or ""),
        "decision_fallback_count": int(state.get("decision_fallback_count", 0)),
        "lane_status": lane_status,
        "delta_summary": delta_summary,
        "generated_at": utc_now(),
    }

    validation_errors: list[str] = []
    for schema_rel, payload in [
        ("schemas/controller_lane_status.schema.json", lane_status),
        ("schemas/controller_delta_summary.schema.json", delta_summary),
        ("schemas/controller_handoff.schema.json", handoff_payload),
    ]:
        validation_errors.extend(
            f"{schema_rel}: {message}"
            for message in validate_payload_with_schema(root, schema_rel, payload)
        )

    if validation_errors and recommendation == "ready":
        recommendation = "needs_review"
        reason = "handoff schema validation failed"
        handoff_payload["recommendation"] = recommendation
        handoff_payload["reason"] = reason

    handoff_payload["validation_errors"] = validation_errors
    write_json(handoff_root / "handoff.json", handoff_payload)

    lines = [
        f"# Controller Handoff ({state['session_id']})",
        "",
        f"- recommendation: {recommendation}",
        f"- reason: {reason}",
        f"- mode: {args.mode}",
        f"- corpus_pack: {corpus_pack}",
        f"- run_id: {run_id or 'none'}",
        f"- consecutive_successes: {state.get('consecutive_successes', 0)}",
        f"- required_success_window: {args.success_window}",
        f"- last_iteration_decision: {state.get('last_iteration_decision', '')}",
        f"- last_selected_by: {state.get('last_selected_by', 'none')}",
        f"- last_commit_sha: {state.get('last_commit_sha', '') or 'none'}",
        f"- decision_fallback_count: {state.get('decision_fallback_count', 0)}",
        f"- iso_lane_pass: {lane_status['iso_lane_pass']}",
        f"- decomposition_lane_pass: {lane_status['decomposition_lane_pass']}",
        f"- fls_lane_pass: {lane_status['fls_lane_pass']}",
        f"- quality_lane_pass: {lane_status['quality_lane_pass']}",
    ]
    if validation_errors:
        lines.append("")
        lines.append("## Validation Errors")
        lines.extend(f"- {message}" for message in validation_errors)

    (handoff_root / "handoff.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    run_registry_candidate_path = handoff_root / "run_registry_candidate.yaml"
    if recommendation == "ready" and run_id:
        candidate = {
            "version": 1,
            "corpus_pack_id": corpus_pack,
            "mode": args.mode,
            "accepted_run_id": run_id,
            "scope_fingerprint": load_scope_fingerprint(root, run_id),
            "accepted_at": utc_now(),
            "accepted_by": f"autonomous-controller:{state['session_id']}",
        }
        candidate_errors = validate_payload_with_schema(
            root,
            "schemas/controller_run_registry_candidate.schema.json",
            candidate,
        )
        if candidate_errors:
            validation_errors.extend(
                f"schemas/controller_run_registry_candidate.schema.json: {message}"
                for message in candidate_errors
            )
            if recommendation == "ready":
                recommendation = "needs_review"
                handoff_payload["recommendation"] = recommendation
                handoff_payload["reason"] = "run-registry candidate validation failed"
                handoff_payload["validation_errors"] = validation_errors
                write_json(handoff_root / "handoff.json", handoff_payload)
                if run_registry_candidate_path.exists():
                    run_registry_candidate_path.unlink()
            else:
                if run_registry_candidate_path.exists():
                    run_registry_candidate_path.unlink()
        else:
            write_yaml(run_registry_candidate_path, candidate)
    elif run_registry_candidate_path.exists():
        run_registry_candidate_path.unlink()

    return recommendation, validation_errors


def write_final_report(
    paths: dict[str, Path],
    state: dict[str, Any],
    observation: dict[str, Any],
    handoff_recommendation: str,
    handoff_validation_errors: list[str],
) -> None:
    lines = [
        f"# Autonomous Controller Final Report ({state['session_id']})",
        "",
        f"- status: {state['status']}",
        f"- iteration: {state['iteration']}",
        f"- no_improvement_count: {state['no_improvement_count']}",
        f"- consecutive_successes: {state['consecutive_successes']}",
        f"- last_orchestrate_run_id: {state.get('last_orchestrate_run_id', '')}",
        f"- last_iteration_decision: {state.get('last_iteration_decision', '')}",
        f"- last_selected_by: {state.get('last_selected_by', 'none')}",
        f"- last_commit_sha: {state.get('last_commit_sha', '') or 'none'}",
        f"- decision_fallback_count: {state.get('decision_fallback_count', 0)}",
        f"- handoff_recommendation: {handoff_recommendation}",
        f"- iso_obligation_coverage: {observation.get('iso_obligation_coverage', 0.0)}",
        f"- target_fanout_gap_count: {observation.get('target_fanout_gap_count', 0)}",
        f"- fls_span_gap_count: {observation.get('fls_span_gap_count', 0)}",
        f"- fls_chapter_coverage: {observation.get('fls_chapter_coverage', 0.0)}",
        f"- quality_gap_count: {observation.get('quality_gap_count', 0)}",
        f"- placeholder_gap_count: {observation.get('placeholder_gap_count', 0)}",
        f"- example_gap_count: {observation.get('example_gap_count', 0)}",
        f"- known_good_alignment_gap_count: {observation.get('known_good_alignment_gap_count', 0)}",
        f"- known_good_alignment_average: {observation.get('known_good_alignment_average', 0.0)}",
        f"- duplication_gap_count: {observation.get('duplication_gap_count', 0)}",
        f"- duplication_exception_missing_count: {observation.get('duplication_exception_missing_count', 0)}",
        f"- rust_signal_gap_count: {observation.get('rust_signal_gap_count', 0)}",
        f"- rust_signal_coverage: {observation.get('rust_signal_coverage', 0.0)}",
        f"- diversity_unique_token_ratio: {observation.get('diversity_unique_token_ratio', 0.0)}",
        f"- weighted_score: {weighted_score(observation)}",
        "",
        "## Reproducible Commands",
        f"- python scripts/autonomous_controller.py --session-id {state['session_id']}",
    ]

    if handoff_validation_errors:
        lines.append("")
        lines.append("## Handoff Validation Errors")
        lines.extend(f"- {message}" for message in handoff_validation_errors)

    paths["final_report_md"].parent.mkdir(parents=True, exist_ok=True)
    paths["final_report_md"].write_text("\n".join(lines) + "\n", encoding="utf-8")


def resolve_corpus_pack(root: Path, requested: str | None) -> str:
    if requested and requested.strip():
        return requested.strip()
    registry = read_yaml(root / "config/corpus_registry.yaml") or {}
    return str(registry.get("default_corpus_pack") or "iso-core-part6").strip()


def interpolate_linear(start: float, target: float, progress: float) -> float:
    bounded = max(0.0, min(1.0, progress))
    return start + ((target - start) * bounded)


def alignment_overrides_for_iteration(root: Path, iteration: int) -> dict[str, Any]:
    policy = read_yaml(root / "config/alignment_policy.yaml") or {}
    progression = policy.get("controller_progression") or {}
    if not bool(progression.get("enabled", False)):
        return {}

    start_iteration = int(progression.get("start_iteration") or 1)
    target_iteration = int(progression.get("target_iteration") or start_iteration)
    target_iteration = max(start_iteration, target_iteration)

    if target_iteration == start_iteration:
        progress = 1.0
    else:
        progress = (iteration - start_iteration) / float(target_iteration - start_iteration)
    progress = max(0.0, min(1.0, progress))

    threshold_defaults = policy.get("thresholds") or {}
    start_thresholds = progression.get("start_thresholds") or {}
    target_thresholds = progression.get("target_thresholds") or {}

    start_global = float(
        start_thresholds.get(
            "min_global_alignment",
            threshold_defaults.get("min_global_alignment", 0.75),
        )
    )
    target_global = float(
        target_thresholds.get(
            "min_global_alignment",
            threshold_defaults.get("min_global_alignment", 0.75),
        )
    )

    start_changed = float(
        start_thresholds.get(
            "min_changed_guideline_alignment",
            threshold_defaults.get("min_changed_guideline_alignment", 0.8),
        )
    )
    target_changed = float(
        target_thresholds.get(
            "min_changed_guideline_alignment",
            threshold_defaults.get("min_changed_guideline_alignment", 0.8),
        )
    )

    start_outliers = float(
        start_thresholds.get(
            "granularity_outliers_allowed",
            threshold_defaults.get("granularity_outliers_allowed", 0),
        )
    )
    target_outliers = float(
        target_thresholds.get(
            "granularity_outliers_allowed",
            threshold_defaults.get("granularity_outliers_allowed", 0),
        )
    )

    start_gate_mode = str(progression.get("start_gate_mode") or policy.get("gate_mode") or "warn")
    target_gate_mode = str(progression.get("target_gate_mode") or start_gate_mode)
    gate_mode = start_gate_mode if progress < 1.0 else target_gate_mode

    return {
        "min_global_alignment": round(interpolate_linear(start_global, target_global, progress), 6),
        "min_changed_guideline_alignment": round(
            interpolate_linear(start_changed, target_changed, progress),
            6,
        ),
        "granularity_outliers_allowed": int(
            round(interpolate_linear(start_outliers, target_outliers, progress))
        ),
        "gate_mode": gate_mode,
        "progress": round(progress, 6),
        "start_iteration": start_iteration,
        "target_iteration": target_iteration,
    }


def suppressed_signatures_from_state(state: dict[str, Any], threshold: int) -> set[str]:
    failure_counts = state.get("bundle_failure_counts") or {}
    return {
        str(signature)
        for signature, count in failure_counts.items()
        if int(count) >= threshold and str(signature)
    }


def apply_failure_feedback(state: dict[str, Any], evaluations: list[dict[str, Any]]) -> None:
    failure_counts = state.get("bundle_failure_counts") or {}
    for evaluation in evaluations:
        signature = str(evaluation.get("bundle_signature") or "")
        if not signature:
            continue
        state_signatures = state.get("candidate_signatures") or []
        if signature not in state_signatures:
            state_signatures.append(signature)
            state["candidate_signatures"] = state_signatures

        if bool(evaluation.get("accepted", False)):
            failure_counts[signature] = 0
        else:
            failure_counts[signature] = int(failure_counts.get(signature, 0)) + 1

    state["bundle_failure_counts"] = failure_counts


def status_exit_code(status: str) -> int:
    if status == "success":
        return EXIT_SUCCESS
    if status == "blocked":
        return EXIT_POLICY_FAIL
    if status == "error":
        return EXIT_RUNTIME_FAIL
    return EXIT_SUCCESS


def main() -> int:
    args = parse_args()
    root = repo_root()
    corpus_pack = resolve_corpus_pack(root, args.corpus_pack)
    paths = controller_paths(root, args.session_id)
    paths["iterations_root"].mkdir(parents=True, exist_ok=True)

    state = load_or_init_state(paths, args, corpus_pack)
    save_state(paths, state)

    current_status = str(state.get("status") or "running")
    if current_status in {"success", "blocked", "error"}:
        print(f"[controller] session already terminal: status={current_status}")
        return status_exit_code(current_status)

    attempted_candidates: list[dict[str, Any]] = []
    handoff_recommendation = str(state.get("handoff_recommendation") or "blocked")
    handoff_validation_errors: list[str] = []

    while int(state["iteration"]) < int(state["max_iterations"]):
        iteration = int(state["iteration"]) + 1
        iteration_dir = paths["iterations_root"] / f"{iteration:03d}"
        iteration_dir.mkdir(parents=True, exist_ok=True)

        alignment_overrides = alignment_overrides_for_iteration(root, iteration)
        write_json(iteration_dir / "known_good_alignment_overrides.json", alignment_overrides)

        pre_profile = args.profile
        if args.full_eval_every > 0 and iteration % args.full_eval_every == 0:
            pre_profile = "full"

        if args.use_orchestrate:
            orchestrate_ok, orchestrate_report = run_orchestrate(
                root,
                args,
                corpus_pack,
                pre_profile,
                iteration_dir / "orchestrate.preobserve.json",
            )
            if str(orchestrate_report.get("run_id") or ""):
                state["last_orchestrate_run_id"] = str(orchestrate_report.get("run_id") or "")
            if not orchestrate_ok:
                state["status"] = "error"
                state["last_iteration_decision"] = "stop-error"
                state["last_selected_by"] = "none"
                state["last_selection_reason"] = "orchestrate_failure"
                append_history(
                    state,
                    iteration,
                    "stop-error",
                    "orchestrate failed before observation",
                    "",
                    selected_by="none",
                    selection_reason="orchestrate_failure",
                )
                save_state(paths, state)
                best_observation = state.get("best_observation") or {}
                if not best_observation:
                    best_observation = state.get("last_observation") or {}
                write_blocker_report(
                    paths,
                    state,
                    "orchestrate failed",
                    best_observation,
                    attempted_candidates,
                )
                handoff_recommendation, handoff_validation_errors = write_handoff_package(
                    root,
                    paths,
                    state,
                    best_observation,
                    args,
                    corpus_pack,
                )
                state["handoff_recommendation"] = handoff_recommendation
                save_state(paths, state)
                write_final_report(
                    paths,
                    state,
                    best_observation,
                    handoff_recommendation,
                    handoff_validation_errors,
                )
                print("[controller] stopped: orchestrate failed")
                return EXIT_RUNTIME_FAIL

        before_observation = observe_repo(
            root,
            iteration_dir / "before",
            known_good_alignment_overrides=alignment_overrides,
        )
        state["last_observation"] = before_observation
        write_json(iteration_dir / "observe.json", before_observation)
        write_dashboard(paths, state, before_observation)

        if success_condition(before_observation):
            state["consecutive_successes"] = int(state["consecutive_successes"]) + 1
        else:
            state["consecutive_successes"] = 0

        if int(state["consecutive_successes"]) >= args.success_window:
            state["status"] = "success"
            state["iteration"] = iteration
            state["best_observation"] = before_observation
            state["last_iteration_decision"] = "stop-success"
            state["last_selected_by"] = "none"
            state["last_selection_reason"] = "convergence_window"
            append_history(
                state,
                iteration,
                "stop-success",
                "success window reached",
                "",
                selected_by="none",
                selection_reason="convergence_window",
            )
            handoff_recommendation, handoff_validation_errors = write_handoff_package(
                root,
                paths,
                state,
                before_observation,
                args,
                corpus_pack,
            )
            state["handoff_recommendation"] = handoff_recommendation
            save_state(paths, state)
            write_final_report(
                paths,
                state,
                before_observation,
                handoff_recommendation,
                handoff_validation_errors,
            )
            print("[controller] success: convergence criteria satisfied")
            return EXIT_SUCCESS

        suppressed_signatures = suppressed_signatures_from_state(
            state,
            args.suppress_after_failures,
        )
        historical_signatures = {
            str(signature) for signature in state.get("candidate_signatures", []) if str(signature)
        }

        candidates = generate_candidates(
            before_observation,
            args.beam_width,
            max_actions_per_bundle=args.max_actions_per_bundle,
            suppressed_signatures=suppressed_signatures,
            historical_signatures=historical_signatures,
        )
        write_json(iteration_dir / "candidates.json", {"candidates": candidates})

        if not candidates:
            state["iteration"] = iteration
            state["no_improvement_count"] = int(state["no_improvement_count"]) + 1
            state["last_iteration_decision"] = "stall"
            state["last_selected_by"] = "none"
            state["last_selection_reason"] = "no_candidates"
            append_history(state, iteration, "stall", "no candidate actions generated", "")
            write_iteration_record(
                iteration_dir,
                state["session_id"],
                iteration,
                before_observation,
                candidates,
                [],
                "stall",
                "",
                selection_source="none",
                selection_reason="no_candidates",
            )
            save_state(paths, state)

            if int(state["no_improvement_count"]) >= int(state["stall_window"]):
                state["status"] = "blocked"
                state["last_iteration_decision"] = "stop-blocked"
                handoff_recommendation, handoff_validation_errors = write_handoff_package(
                    root,
                    paths,
                    state,
                    before_observation,
                    args,
                    corpus_pack,
                )
                state["handoff_recommendation"] = handoff_recommendation
                save_state(paths, state)
                write_blocker_report(
                    paths,
                    state,
                    "no candidate actions generated within stall window",
                    before_observation,
                    attempted_candidates,
                )
                write_final_report(
                    paths,
                    state,
                    before_observation,
                    handoff_recommendation,
                    handoff_validation_errors,
                )
                print("[controller] blocked: no candidate actions")
                return EXIT_POLICY_FAIL
            if args.single_iteration:
                print("[controller] single-iteration complete: decision=stall")
                return EXIT_SUCCESS
            continue

        candidate_map = {
            str(candidate.get("candidate_id") or ""): candidate for candidate in candidates
        }

        decision_packet = build_decision_packet(
            state["session_id"],
            iteration,
            before_observation,
            candidates,
            suppressed_signatures,
            historical_signatures,
            alignment_overrides,
            {
                "beam_width": args.beam_width,
                "full_eval_top_k": args.full_eval_top_k,
                "profile": args.profile,
            },
        )
        selection_resolution = resolve_candidate_selection(
            root,
            decision_packet,
            iteration_dir,
            policy_path=args.decision_policy,
        )
        write_json(iteration_dir / "selection_resolution.json", selection_resolution)

        selection_source = str(selection_resolution.get("selection_source") or "deterministic")
        selection_reason = str(
            selection_resolution.get("resolution_reason") or "deterministic_policy"
        )
        state["last_selected_by"] = selection_source
        state["last_selection_reason"] = selection_reason
        if selection_source == "fallback":
            state["decision_fallback_count"] = int(state.get("decision_fallback_count") or 0) + 1

        if selection_reason.startswith("fallback_disallowed:"):
            state["status"] = "error"
            state["iteration"] = iteration
            state["last_iteration_decision"] = "stop-error"
            append_history(
                state,
                iteration,
                "stop-error",
                "llm fallback disabled and decision invalid",
                "",
                selected_by=selection_source,
                selection_reason=selection_reason,
            )
            write_iteration_record(
                iteration_dir,
                state["session_id"],
                iteration,
                before_observation,
                candidates,
                [],
                "stop-error",
                "",
                selection_source=selection_source,
                selection_reason=selection_reason,
            )
            save_state(paths, state)
            write_blocker_report(
                paths,
                state,
                "llm decision invalid and deterministic fallback disabled",
                before_observation,
                attempted_candidates,
            )
            handoff_recommendation, handoff_validation_errors = write_handoff_package(
                root,
                paths,
                state,
                before_observation,
                args,
                corpus_pack,
            )
            state["handoff_recommendation"] = handoff_recommendation
            save_state(paths, state)
            write_final_report(
                paths,
                state,
                before_observation,
                handoff_recommendation,
                handoff_validation_errors,
            )
            print("[controller] stopped: llm fallback disabled")
            return EXIT_RUNTIME_FAIL

        ordered_candidate_ids = [
            str(item)
            for item in (selection_resolution.get("ordered_candidate_ids") or [])
            if str(item)
        ]
        if not ordered_candidate_ids:
            ordered_candidate_ids = [
                str(candidate.get("candidate_id") or "")
                for candidate in candidates
                if str(candidate.get("candidate_id") or "")
            ]

        ordered_candidates: list[dict[str, Any]] = []
        for candidate_id in ordered_candidate_ids:
            candidate = candidate_map.get(candidate_id)
            if candidate is not None:
                ordered_candidates.append(candidate)
        if not ordered_candidates:
            ordered_candidates = candidates

        quick_evaluations: list[dict[str, Any]] = []
        for candidate in ordered_candidates:
            if args.dry_run:
                quick_evaluations.append(
                    {
                        "candidate_id": candidate.get("candidate_id"),
                        "bundle_signature": candidate.get("bundle_signature"),
                        "accepted": False,
                        "observation": before_observation,
                        "regressions": ["dry-run"],
                        "note": "dry-run candidate skipped",
                        "weighted_score": weighted_score(before_observation),
                        "metric_vector": metric_vector(before_observation),
                        "evaluation_profile": "quick",
                        "mutation_footprint_estimate": int(
                            candidate.get("mutation_footprint_estimate") or 0
                        ),
                    }
                )
                continue

            evaluation = evaluate_candidate(
                root,
                args,
                corpus_pack,
                iteration_dir,
                before_observation,
                candidate,
                "quick",
                known_good_alignment_overrides=alignment_overrides,
            )
            quick_evaluations.append(evaluation)
            attempted_candidates.append(
                {
                    "iteration": iteration,
                    "candidate_id": evaluation["candidate_id"],
                    "bundle_signature": evaluation.get("bundle_signature", ""),
                    "accepted": evaluation["accepted"],
                    "profile": "quick",
                    "note": evaluation.get("note", ""),
                }
            )

        quick_passes = [item for item in quick_evaluations if bool(item.get("accepted", False))]
        quick_passes.sort(key=lambda item: evaluation_sort_key(before_observation, item))

        full_evaluations: list[dict[str, Any]] = []
        if not args.dry_run and quick_passes:
            for quick_eval in quick_passes[: max(1, args.full_eval_top_k)]:
                candidate_id = str(quick_eval.get("candidate_id") or "")
                candidate = candidate_map.get(candidate_id)
                if candidate is None:
                    continue
                full_eval = evaluate_candidate(
                    root,
                    args,
                    corpus_pack,
                    iteration_dir,
                    before_observation,
                    candidate,
                    "full",
                    known_good_alignment_overrides=alignment_overrides,
                )
                full_eval["quick_reference"] = {
                    "accepted": bool(quick_eval.get("accepted", False)),
                    "metric_vector": quick_eval.get("metric_vector"),
                    "weighted_score": quick_eval.get("weighted_score"),
                }
                full_evaluations.append(full_eval)
                attempted_candidates.append(
                    {
                        "iteration": iteration,
                        "candidate_id": full_eval["candidate_id"],
                        "bundle_signature": full_eval.get("bundle_signature", ""),
                        "accepted": full_eval["accepted"],
                        "profile": "full",
                        "note": full_eval.get("note", ""),
                    }
                )

        final_evaluations = full_evaluations if full_evaluations else quick_evaluations
        write_json(
            iteration_dir / "evaluation.json",
            {
                "quick": quick_evaluations,
                "full": full_evaluations,
                "final": final_evaluations,
            },
        )

        apply_failure_feedback(state, final_evaluations)
        accepted_candidates = [
            item for item in final_evaluations if bool(item.get("accepted", False))
        ]
        accepted_candidates.sort(key=lambda item: evaluation_sort_key(before_observation, item))

        if not accepted_candidates:
            state["iteration"] = iteration
            state["no_improvement_count"] = int(state["no_improvement_count"]) + 1
            state["last_iteration_decision"] = "rejected"
            append_history(
                state,
                iteration,
                "rejected",
                "no acceptable candidate",
                "",
                selected_by=selection_source,
                selection_reason=selection_reason,
            )
            write_iteration_record(
                iteration_dir,
                state["session_id"],
                iteration,
                before_observation,
                candidates,
                final_evaluations,
                "rejected",
                "",
                selection_source=selection_source,
                selection_reason=selection_reason,
            )
            save_state(paths, state)

            if int(state["no_improvement_count"]) >= int(state["stall_window"]):
                state["status"] = "blocked"
                state["last_iteration_decision"] = "stop-blocked"
                handoff_recommendation, handoff_validation_errors = write_handoff_package(
                    root,
                    paths,
                    state,
                    before_observation,
                    args,
                    corpus_pack,
                )
                state["handoff_recommendation"] = handoff_recommendation
                save_state(paths, state)
                write_blocker_report(
                    paths,
                    state,
                    "no acceptable candidate within stall window",
                    before_observation,
                    attempted_candidates,
                )
                write_final_report(
                    paths,
                    state,
                    before_observation,
                    handoff_recommendation,
                    handoff_validation_errors,
                )
                print("[controller] blocked: no acceptable candidate")
                return EXIT_POLICY_FAIL
            if args.single_iteration:
                print("[controller] single-iteration complete: decision=rejected")
                return EXIT_SUCCESS
            continue

        selected = accepted_candidates[0]
        selected_candidate_id = str(selected.get("candidate_id") or "")
        selected_candidate = candidate_map[selected_candidate_id]
        selected_touched_guidelines = touched_guideline_ids(selected_candidate)
        selected_profile = str(selected.get("evaluation_profile") or "quick")
        commit_report: dict[str, Any] = {
            "requested": bool(args.commit_on_accept and not args.dry_run),
            "ok": True,
            "committed": False,
            "commit_sha": "",
            "message": "commit not requested",
            "output": "",
        }

        with tempfile.TemporaryDirectory(prefix="controller-accept-") as temp_dir:
            backup_root = Path(temp_dir)
            snapshot_workspace(root, backup_root)

            apply_result = apply_candidate(root, selected_candidate)
            write_json(iteration_dir / "accepted_apply_result.json", apply_result)

            accept_orchestrate_ok = True
            accept_orchestrate_report = {"enabled": False}
            if args.use_orchestrate:
                accept_orchestrate_ok, accept_orchestrate_report = run_orchestrate(
                    root,
                    args,
                    corpus_pack,
                    selected_profile,
                    iteration_dir / "orchestrate.accept.json",
                )
                run_id = str(accept_orchestrate_report.get("run_id") or "")
                if run_id:
                    state["last_orchestrate_run_id"] = run_id

            after_observation = observe_repo(
                root,
                iteration_dir / "after",
                known_good_alignment_overrides=alignment_overrides,
            )
            regressions = regression_flags(before_observation, after_observation)
            accepted = bool(apply_result.get("ok", False)) and accept_orchestrate_ok
            accepted = accepted and improves(before_observation, after_observation)
            accepted = accepted and not regressions

            if accepted and selected_touched_guidelines:
                stagnant = stagnant_quality_guidelines(
                    before_observation,
                    after_observation,
                    selected_touched_guidelines,
                )
                if stagnant:
                    accepted = False
                    regressions.append(
                        "touched_guideline_quality_progress_missing:"
                        + ",".join(sorted(stagnant))
                    )

            promotion_ready_for_commit = True
            if (
                accepted
                and args.commit_on_accept
                and not args.dry_run
                and args.strict_promotion
            ):
                promotion_observation = observe_repo(
                    root,
                    iteration_dir / "promotion",
                    known_good_alignment_overrides=alignment_overrides,
                    strict_gate_mode=True,
                )
                promotion_ready_for_commit = success_condition(promotion_observation)
                if not promotion_ready_for_commit:
                    commit_report["message"] = "strict promotion gate failed; commit deferred"
                    commit_report["output"] = (
                        "strict promotion requires hard/iso/decomposition/fls/quality lanes to pass"
                    )

            if (
                accepted
                and args.commit_on_accept
                and not args.dry_run
                and promotion_ready_for_commit
            ):
                commit_report = commit_iteration(
                    root,
                    state["session_id"],
                    iteration,
                    selected_candidate_id,
                    selection_source,
                    selected_profile,
                    str(state.get("last_orchestrate_run_id") or ""),
                    allow_main_branch_commits=bool(args.allow_main_branch_commits),
                )
                write_json(iteration_dir / "commit.json", commit_report)
                if not bool(commit_report.get("ok", False)):
                    accepted = False
                    regressions.append("git_commit_failed")
                elif bool(commit_report.get("committed", False)):
                    state["last_commit_sha"] = str(commit_report.get("commit_sha") or "")
            write_json(iteration_dir / "commit.json", commit_report)

            if not accepted:
                restore_workspace(root, backup_root)
                state["iteration"] = iteration
                state["no_improvement_count"] = int(state["no_improvement_count"]) + 1
                state["last_iteration_decision"] = "rejected"
                append_history(
                    state,
                    iteration,
                    "rejected",
                    "selected candidate failed acceptance check",
                    selected_candidate_id,
                    selected_by=selection_source,
                    selection_reason=selection_reason,
                )

                failure_counts = state.get("bundle_failure_counts") or {}
                signature = str(selected.get("bundle_signature") or "")
                if signature:
                    failure_counts[signature] = int(failure_counts.get(signature, 0)) + 1
                state["bundle_failure_counts"] = failure_counts

                write_iteration_record(
                    iteration_dir,
                    state["session_id"],
                    iteration,
                    before_observation,
                    candidates,
                    final_evaluations,
                    "rejected",
                    selected_candidate_id,
                    selection_source=selection_source,
                    selection_reason=selection_reason,
                    commit=commit_report,
                )
                save_state(paths, state)

                if int(state["no_improvement_count"]) >= int(state["stall_window"]):
                    state["status"] = "blocked"
                    state["last_iteration_decision"] = "stop-blocked"
                    handoff_recommendation, handoff_validation_errors = write_handoff_package(
                        root,
                        paths,
                        state,
                        before_observation,
                        args,
                        corpus_pack,
                    )
                    state["handoff_recommendation"] = handoff_recommendation
                    save_state(paths, state)
                    write_blocker_report(
                        paths,
                        state,
                        "selected candidate repeatedly failed acceptance",
                        before_observation,
                        attempted_candidates,
                    )
                    write_final_report(
                        paths,
                        state,
                        before_observation,
                        handoff_recommendation,
                        handoff_validation_errors,
                    )
                    print("[controller] blocked: selected candidate failed")
                    return EXIT_POLICY_FAIL
                if args.single_iteration:
                    print("[controller] single-iteration complete: decision=rejected")
                    return EXIT_SUCCESS
                continue

        state["iteration"] = iteration
        state["best_observation"] = after_observation
        state["no_improvement_count"] = 0
        state["last_iteration_decision"] = "accepted"
        state["consecutive_successes"] = (
            int(state["consecutive_successes"]) + 1 if success_condition(after_observation) else 0
        )
        append_history(
            state,
            iteration,
            "accepted",
            "candidate accepted",
            selected_candidate_id,
            selected_by=selection_source,
            selection_reason=selection_reason,
            commit_sha=str(commit_report.get("commit_sha") or ""),
        )
        write_iteration_record(
            iteration_dir,
            state["session_id"],
            iteration,
            before_observation,
            candidates,
            final_evaluations,
            "accepted",
            selected_candidate_id,
            after_observation,
            selection_source=selection_source,
            selection_reason=selection_reason,
            commit=commit_report,
        )
        save_state(paths, state)
        if args.single_iteration:
            print("[controller] single-iteration complete: decision=accepted")
            return EXIT_SUCCESS

    state["status"] = "blocked"
    state["last_iteration_decision"] = "stop-blocked"
    save_state(paths, state)
    observation = state.get("best_observation") or {}
    if not observation:
        observation = state.get("last_observation") or {}
    write_blocker_report(
        paths,
        state,
        "max iterations reached",
        observation,
        attempted_candidates,
    )
    handoff_recommendation, handoff_validation_errors = write_handoff_package(
        root,
        paths,
        state,
        observation,
        args,
        corpus_pack,
    )
    state["handoff_recommendation"] = handoff_recommendation
    save_state(paths, state)
    write_final_report(
        paths,
        state,
        observation,
        handoff_recommendation,
        handoff_validation_errors,
    )
    print("[controller] blocked: max iterations reached")
    return EXIT_POLICY_FAIL


if __name__ == "__main__":
    sys.exit(main())
