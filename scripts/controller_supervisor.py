#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from _common import (
    EXIT_POLICY_FAIL,
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    read_json,
    read_yaml,
    repo_root,
    run_command,
    utc_now,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run fresh-process supervisor for controller loops"
    )
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--max-loops", type=int, default=30)
    parser.add_argument("--spawn-command", type=str)
    parser.add_argument("--controller-args-file", type=Path)
    parser.add_argument(
        "--controller-arg",
        action="append",
        default=[],
        help="Additional arg passed to autonomous_controller worker",
    )
    parser.add_argument("--poll-interval-seconds", type=float, default=0.0)
    parser.add_argument("--force-recover-lock", action="store_true")
    return parser.parse_args()


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_lock_payload(lock_path: Path) -> dict[str, Any]:
    if not lock_path.exists():
        return {}
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def acquire_lock(lock_path: Path, force_recover_lock: bool) -> tuple[bool, str]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        payload = read_lock_payload(lock_path)
        pid = int(payload.get("pid") or 0)
        if process_alive(pid) and not force_recover_lock:
            return False, f"active supervisor lock held by pid={pid}"
        lock_path.unlink()

    lock_payload = {
        "pid": os.getpid(),
        "created_at": utc_now(),
    }
    lock_path.write_text(json.dumps(lock_payload, indent=2) + "\n", encoding="utf-8")
    return True, ""


def load_controller_args(root: Path, args: argparse.Namespace) -> list[str]:
    loaded_args: list[str] = []
    if args.controller_args_file:
        payload_path = root / args.controller_args_file
        if not payload_path.exists():
            raise FileNotFoundError(f"missing controller args file: {payload_path}")

        payload = read_yaml(payload_path)
        if isinstance(payload, list):
            loaded_args = [str(item) for item in payload if str(item).strip()]
        elif isinstance(payload, dict):
            values = payload.get("controller_args") or []
            if not isinstance(values, list):
                raise ValueError("controller_args must be a list")
            loaded_args = [str(item) for item in values if str(item).strip()]
        else:
            raise ValueError("controller args file must be list or object")

    cli_args = [str(item) for item in (args.controller_arg or []) if str(item).strip()]
    return [*loaded_args, *cli_args]


def build_worker_command(
    root: Path,
    session_id: str,
    spawn_command: str | None,
    controller_args: list[str],
) -> list[str]:
    if not spawn_command:
        return [
            sys.executable,
            "scripts/autonomous_controller.py",
            "--resume-session",
            session_id,
            "--single-iteration",
            *controller_args,
        ]

    tokens = shlex.split(spawn_command)
    placeholders = {
        "session_id": session_id,
        "repo_root": str(root),
    }

    rendered: list[str] = []
    for token in tokens:
        if token == "{controller_args}":
            rendered.extend(controller_args)
            continue
        updated = token
        for key, value in placeholders.items():
            updated = updated.replace(f"{{{key}}}", value)
        rendered.append(updated)

    if "--single-iteration" not in rendered:
        rendered.append("--single-iteration")
    return rendered


def load_controller_state(root: Path, session_id: str) -> dict[str, Any]:
    path = root / ".cache" / "controller" / session_id / "state.json"
    if not path.exists():
        return {
            "status": "running",
            "iteration": 0,
            "last_iteration_decision": "",
        }
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {
            "status": "running",
            "iteration": 0,
            "last_iteration_decision": "",
        }
    return payload


def save_supervisor_state(path: Path, payload: dict[str, Any]) -> None:
    payload["updated_at"] = utc_now()
    write_json(path, payload)


def validate_supervisor_state(root: Path, payload: dict[str, Any]) -> list[str]:
    schema = read_json(root / "schemas/controller_supervisor_state.schema.json")
    validator = Draft202012Validator(schema)
    return [error.message for error in validator.iter_errors(payload)]


def exit_code_from_status(status: str) -> int:
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

    session_root = root / ".cache" / "controller" / args.session_id
    session_root.mkdir(parents=True, exist_ok=True)
    state_path = session_root / "supervisor_state.json"
    lock_path = session_root / "supervisor.lock"

    ok, lock_error = acquire_lock(lock_path, args.force_recover_lock)
    if not ok:
        print(f"[controller-supervisor][error] {lock_error}")
        return EXIT_RUNTIME_FAIL

    try:
        try:
            controller_args = load_controller_args(root, args)
        except (FileNotFoundError, ValueError) as exc:
            print(f"[controller-supervisor][error] {exc}")
            return EXIT_RUNTIME_FAIL

        state = {
            "version": 1,
            "session_id": args.session_id,
            "status": "running",
            "loop_index": 0,
            "max_loops": int(args.max_loops),
            "last_exit_code": 0,
            "last_iteration_decision": "",
            "controller_status": "running",
            "lock_path": str(lock_path.relative_to(root)),
            "runs": [],
            "started_at": utc_now(),
            "updated_at": utc_now(),
        }
        if state_path.exists():
            existing = read_json(state_path)
            if isinstance(existing, dict):
                for key, value in state.items():
                    existing.setdefault(key, value)
                state = existing
        save_supervisor_state(state_path, state)

        for loop_index in range(int(state.get("loop_index", 0)) + 1, int(args.max_loops) + 1):
            controller_state = load_controller_state(root, args.session_id)
            controller_status = str(controller_state.get("status") or "running")
            if controller_status in {"success", "blocked", "error"}:
                state["status"] = controller_status
                state["controller_status"] = controller_status
                save_supervisor_state(state_path, state)
                break

            command = build_worker_command(
                root,
                args.session_id,
                args.spawn_command,
                controller_args,
            )
            started_at = utc_now()
            completed = run_command(command, cwd=root)
            completed_at = utc_now()

            controller_state = load_controller_state(root, args.session_id)
            controller_status = str(controller_state.get("status") or "running")
            iteration = int(controller_state.get("iteration") or 0)
            iteration_decision = str(controller_state.get("last_iteration_decision") or "")

            run_record = {
                "loop_index": loop_index,
                "command": command,
                "return_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "started_at": started_at,
                "completed_at": completed_at,
                "controller_status": controller_status,
                "iteration": iteration,
                "iteration_decision": iteration_decision,
            }
            state_runs = state.get("runs") or []
            state_runs.append(run_record)
            state["runs"] = state_runs
            state["loop_index"] = loop_index
            state["last_exit_code"] = completed.returncode
            state["last_iteration_decision"] = iteration_decision
            state["controller_status"] = controller_status

            if completed.returncode != 0 and controller_status == "running":
                state["status"] = "error"
                save_supervisor_state(state_path, state)
                print(
                    "[controller-supervisor][error] worker failed while controller still running"
                )
                return EXIT_RUNTIME_FAIL

            if controller_status in {"success", "blocked", "error"}:
                state["status"] = controller_status
                save_supervisor_state(state_path, state)
                break

            state["status"] = "running"
            save_supervisor_state(state_path, state)

            if args.poll_interval_seconds > 0:
                time.sleep(args.poll_interval_seconds)

        if str(state.get("status") or "running") == "running":
            state["status"] = "blocked"
            save_supervisor_state(state_path, state)

        errors = validate_supervisor_state(root, state)
        if errors:
            state["status"] = "error"
            save_supervisor_state(state_path, state)
            print(f"[controller-supervisor][error] schema validation failed: {errors}")
            return EXIT_RUNTIME_FAIL

        final_status = str(state.get("status") or "error")
        print(
            "[controller-supervisor] "
            f"status={final_status} loops={state.get('loop_index', 0)} "
            f"last_decision={state.get('last_iteration_decision', '')}"
        )
        return exit_code_from_status(final_status)
    finally:
        if lock_path.exists():
            lock_path.unlink()


if __name__ == "__main__":
    sys.exit(main())
