#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from _common import (
    EXIT_POLICY_FAIL,
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    read_yaml,
    repo_root,
    run_command,
    utc_now,
    write_json,
)
from controller_actions import apply_candidate, generate_candidates
from controller_observe import observe_repo
from controller_scoring import improves, metric_vector, regression_flags, weighted_score

MUTABLE_PATHS = [
    "data/coverage_matrix.csv",
    "data/decomposition_report.yaml",
    "data/guideline_categories.yaml",
    "data/target_scope.yaml",
    "data/todo_guidelines.yaml",
    "tests/guidelines",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run autonomous convergence controller")
    parser.add_argument("--session-id", default="autonomous")
    parser.add_argument("--mode", choices=["change", "growth"], default="growth")
    parser.add_argument("--profile", choices=["quick", "full"], default="quick")
    parser.add_argument("--max-iterations", type=int, default=30)
    parser.add_argument("--stall-window", type=int, default=5)
    parser.add_argument("--beam-width", type=int, default=4)
    parser.add_argument("--full-eval-every", type=int, default=3)
    parser.add_argument("--success-window", type=int, default=3)
    parser.add_argument("--commit-each-accept", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--use-orchestrate", action="store_true")
    parser.add_argument("--allow-bootstrap", action="store_true")
    parser.add_argument("--corpus-pack")
    return parser.parse_args()


def controller_paths(root: Path, session_id: str) -> dict[str, Path]:
    session_root = root / ".cache" / "controller" / session_id
    return {
        "session_root": session_root,
        "iterations_root": session_root / "iterations",
        "state_json": session_root / "state.json",
        "dashboard_md": session_root / "dashboard.md",
        "final_report_md": session_root / "final_report.md",
        "blocker_json": session_root / "blocker_report.json",
    }


def load_or_init_state(paths: dict[str, Path], args: argparse.Namespace) -> dict[str, Any]:
    state_path = paths["state_json"]
    if state_path.exists():
        with state_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

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
        "history": [],
        "started_at": utc_now(),
        "updated_at": utc_now(),
    }


def save_state(paths: dict[str, Path], state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    write_json(paths["state_json"], state)


def append_history(
    state: dict[str, Any], iteration: int, decision: str, note: str, candidate_id: str
) -> None:
    state["history"].append(
        {
            "iteration": iteration,
            "decision": decision,
            "candidate_id": candidate_id,
            "recorded_at": utc_now(),
            "note": note,
        }
    )


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
        f"- iso_obligation_coverage: {observation.get('iso_obligation_coverage', 0.0)}",
        f"- target_fanout_gap_count: {observation.get('target_fanout_gap_count', 0)}",
        f"- fls_span_gap_count: {observation.get('fls_span_gap_count', 0)}",
        f"- quality_gap_count: {observation.get('quality_gap_count', 0)}",
        f"- placeholder_gap_count: {observation.get('placeholder_gap_count', 0)}",
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


def run_orchestrate_if_enabled(
    root: Path,
    args: argparse.Namespace,
    iteration: int,
    iteration_dir: Path,
) -> tuple[bool, dict[str, Any]]:
    if not args.use_orchestrate:
        return True, {"enabled": False}

    corpus_pack = args.corpus_pack
    if not corpus_pack:
        registry = read_yaml(root / "config/corpus_registry.yaml") or {}
        corpus_pack = str(registry.get("default_corpus_pack") or "").strip()

    profile = args.profile
    if args.full_eval_every > 0 and iteration % args.full_eval_every == 0:
        profile = "full"

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
    report = {
        "enabled": True,
        "command": " ".join(command),
        "return_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    write_json(iteration_dir / "orchestrate.json", report)
    return completed.returncode == 0, report


def evaluate_candidate(
    root: Path,
    iteration_dir: Path,
    before_observation: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    candidate_id = str(candidate.get("candidate_id") or "unknown")
    candidate_dir = iteration_dir / f"candidate_{candidate_id}"
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
                "accepted": False,
                "observation": before_observation,
                "regressions": ["apply_candidate_failed"],
                "note": "candidate apply or regeneration failed",
                "weighted_score": weighted_score(before_observation),
            }

        after_observation = observe_repo(root, candidate_dir / "observation")
        regressions = regression_flags(before_observation, after_observation)
        improved = improves(before_observation, after_observation)
        accepted = improved and not regressions

        evaluation = {
            "candidate_id": candidate_id,
            "accepted": accepted,
            "observation": after_observation,
            "regressions": regressions,
            "note": "improved" if accepted else "not improved or regressed",
            "weighted_score": weighted_score(after_observation),
            "metric_vector": metric_vector(after_observation),
        }

        write_json(candidate_dir / "evaluation.json", evaluation)
        restore_workspace(root, backup_root)
        return evaluation


def commit_iteration(root: Path, iteration: int, candidate_id: str) -> tuple[bool, str]:
    add_command = [
        "git",
        "add",
        "data/todo_guidelines.yaml",
        "data/coverage_matrix.csv",
        "data/guideline_categories.yaml",
        "data/target_scope.yaml",
        "data/decomposition_report.yaml",
        "tests/guidelines",
    ]
    add_result = run_command(add_command, cwd=root)
    if add_result.returncode != 0:
        return False, add_result.stderr or add_result.stdout

    status_result = run_command(["git", "status", "--short"], cwd=root)
    if status_result.returncode != 0:
        return False, status_result.stderr or status_result.stdout
    if not status_result.stdout.strip():
        return True, "no changes to commit"

    commit_message = f"chore(controller): accept iteration {iteration} {candidate_id}"
    commit_result = run_command(["git", "commit", "-m", commit_message], cwd=root)
    if commit_result.returncode != 0:
        return False, commit_result.stderr or commit_result.stdout
    return True, commit_result.stdout


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


def write_final_report(
    paths: dict[str, Path], state: dict[str, Any], observation: dict[str, Any]
) -> None:
    lines = [
        f"# Autonomous Controller Final Report ({state['session_id']})",
        "",
        f"- status: {state['status']}",
        f"- iteration: {state['iteration']}",
        f"- no_improvement_count: {state['no_improvement_count']}",
        f"- consecutive_successes: {state['consecutive_successes']}",
        f"- iso_obligation_coverage: {observation.get('iso_obligation_coverage', 0.0)}",
        f"- target_fanout_gap_count: {observation.get('target_fanout_gap_count', 0)}",
        f"- fls_span_gap_count: {observation.get('fls_span_gap_count', 0)}",
        f"- fls_chapter_coverage: {observation.get('fls_chapter_coverage', 0.0)}",
        f"- quality_gap_count: {observation.get('quality_gap_count', 0)}",
        f"- placeholder_gap_count: {observation.get('placeholder_gap_count', 0)}",
        f"- example_gap_count: {observation.get('example_gap_count', 0)}",
        f"- weighted_score: {weighted_score(observation)}",
        "",
        "## Reproducible Commands",
        f"- python scripts/autonomous_controller.py --session-id {state['session_id']}",
    ]
    paths["final_report_md"].parent.mkdir(parents=True, exist_ok=True)
    paths["final_report_md"].write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    root = repo_root()
    paths = controller_paths(root, args.session_id)
    paths["iterations_root"].mkdir(parents=True, exist_ok=True)

    state = load_or_init_state(paths, args)
    save_state(paths, state)

    attempted_candidates: list[dict[str, Any]] = []

    while state["iteration"] < state["max_iterations"]:
        iteration = int(state["iteration"]) + 1
        iteration_dir = paths["iterations_root"] / f"{iteration:03d}"
        iteration_dir.mkdir(parents=True, exist_ok=True)

        orchestrate_ok, orchestrate_report = run_orchestrate_if_enabled(
            root, args, iteration, iteration_dir
        )
        if not orchestrate_ok:
            state["status"] = "error"
            append_history(
                state,
                iteration,
                "stop-error",
                "orchestrate failed before observation",
                "",
            )
            save_state(paths, state)
            write_blocker_report(
                paths,
                state,
                "orchestrate failed",
                state.get("best_observation") or {},
                attempted_candidates,
            )
            write_final_report(paths, state, state.get("best_observation") or {})
            print("[controller] stopped: orchestrate failed")
            return EXIT_RUNTIME_FAIL

        write_json(iteration_dir / "orchestrate.summary.json", orchestrate_report)
        before_observation = observe_repo(root, iteration_dir / "before")
        write_json(iteration_dir / "observe.json", before_observation)
        write_dashboard(paths, state, before_observation)

        if success_condition(before_observation):
            state["consecutive_successes"] = int(state["consecutive_successes"]) + 1
        else:
            state["consecutive_successes"] = 0

        if state["consecutive_successes"] >= args.success_window:
            state["status"] = "success"
            state["iteration"] = iteration
            state["best_observation"] = before_observation
            append_history(state, iteration, "stop-success", "success window reached", "")
            save_state(paths, state)
            write_final_report(paths, state, before_observation)
            print("[controller] success: convergence criteria satisfied")
            return EXIT_SUCCESS

        candidates = generate_candidates(before_observation, args.beam_width)
        write_json(iteration_dir / "candidates.json", {"candidates": candidates})

        if not candidates:
            state["iteration"] = iteration
            state["no_improvement_count"] = int(state["no_improvement_count"]) + 1
            append_history(state, iteration, "stall", "no candidate actions generated", "")
            save_state(paths, state)

            if state["no_improvement_count"] >= state["stall_window"]:
                state["status"] = "blocked"
                save_state(paths, state)
                write_blocker_report(
                    paths,
                    state,
                    "no candidate actions generated within stall window",
                    before_observation,
                    attempted_candidates,
                )
                write_final_report(paths, state, before_observation)
                print("[controller] blocked: no candidate actions")
                return EXIT_POLICY_FAIL
            continue

        evaluations = []
        for candidate in candidates:
            if args.dry_run:
                evaluations.append(
                    {
                        "candidate_id": candidate.get("candidate_id"),
                        "accepted": False,
                        "observation": before_observation,
                        "regressions": ["dry-run"],
                        "note": "dry-run candidate skipped",
                        "weighted_score": weighted_score(before_observation),
                        "metric_vector": metric_vector(before_observation),
                    }
                )
                continue

            evaluation = evaluate_candidate(root, iteration_dir, before_observation, candidate)
            evaluations.append(evaluation)
            attempted_candidates.append(
                {
                    "iteration": iteration,
                    "candidate_id": evaluation["candidate_id"],
                    "accepted": evaluation["accepted"],
                    "note": evaluation.get("note", ""),
                }
            )

        write_json(iteration_dir / "evaluation.json", {"evaluations": evaluations})

        accepted_candidates = [item for item in evaluations if item.get("accepted", False)]
        selected: dict[str, Any] | None = None
        if accepted_candidates:
            accepted_candidates.sort(
                key=lambda item: (
                    tuple(item.get("metric_vector") or metric_vector(before_observation)),
                    -float(item.get("weighted_score") or 0.0),
                )
            )
            selected = accepted_candidates[0]

        if selected is None:
            state["iteration"] = iteration
            state["no_improvement_count"] = int(state["no_improvement_count"]) + 1
            append_history(state, iteration, "rejected", "no acceptable candidate", "")
            save_state(paths, state)

            if state["no_improvement_count"] >= state["stall_window"]:
                state["status"] = "blocked"
                save_state(paths, state)
                write_blocker_report(
                    paths,
                    state,
                    "no acceptable candidate within stall window",
                    before_observation,
                    attempted_candidates,
                )
                write_final_report(paths, state, before_observation)
                print("[controller] blocked: no acceptable candidate")
                return EXIT_POLICY_FAIL
            continue

        selected_candidate_id = str(selected.get("candidate_id") or "")
        selected_candidate = next(
            candidate
            for candidate in candidates
            if str(candidate.get("candidate_id") or "") == selected_candidate_id
        )

        with tempfile.TemporaryDirectory(prefix="controller-accept-") as temp_dir:
            backup_root = Path(temp_dir)
            snapshot_workspace(root, backup_root)
            apply_result = apply_candidate(root, selected_candidate)
            write_json(iteration_dir / "accepted_apply_result.json", apply_result)

            after_observation = observe_repo(root, iteration_dir / "after")
            regressions = regression_flags(before_observation, after_observation)
            accepted = bool(apply_result.get("ok", False)) and improves(
                before_observation, after_observation
            )
            accepted = accepted and not regressions

            if accepted and args.commit_each_accept and not args.dry_run:
                ok, output = commit_iteration(root, iteration, selected_candidate_id)
                write_json(
                    iteration_dir / "commit.json",
                    {
                        "ok": ok,
                        "output": output,
                        "candidate_id": selected_candidate_id,
                    },
                )
                if not ok:
                    accepted = False
                    regressions.append("git_commit_failed")

            if not accepted:
                restore_workspace(root, backup_root)
                state["iteration"] = iteration
                state["no_improvement_count"] = int(state["no_improvement_count"]) + 1
                append_history(
                    state,
                    iteration,
                    "rejected",
                    "selected candidate failed acceptance check",
                    selected_candidate_id,
                )
                save_state(paths, state)

                if state["no_improvement_count"] >= state["stall_window"]:
                    state["status"] = "blocked"
                    save_state(paths, state)
                    write_blocker_report(
                        paths,
                        state,
                        "selected candidate repeatedly failed acceptance",
                        before_observation,
                        attempted_candidates,
                    )
                    write_final_report(paths, state, before_observation)
                    print("[controller] blocked: selected candidate failed")
                    return EXIT_POLICY_FAIL
                continue

        state["iteration"] = iteration
        state["best_observation"] = after_observation
        state["no_improvement_count"] = 0
        state["consecutive_successes"] = (
            int(state["consecutive_successes"]) + 1 if success_condition(after_observation) else 0
        )
        append_history(
            state,
            iteration,
            "accepted",
            "candidate accepted",
            selected_candidate_id,
        )
        save_state(paths, state)

    state["status"] = "blocked"
    save_state(paths, state)
    observation = state.get("best_observation") or {}
    write_blocker_report(
        paths,
        state,
        "max iterations reached",
        observation,
        attempted_candidates,
    )
    write_final_report(paths, state, observation)
    print("[controller] blocked: max iterations reached")
    return EXIT_POLICY_FAIL


if __name__ == "__main__":
    sys.exit(main())
