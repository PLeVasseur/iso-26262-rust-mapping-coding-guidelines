#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from semantic_backend_client import SemanticBackendConfig, check_semantic_backend

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3

DEFAULT_RUNTIME_DIR = ".cache/sqlite_kb/runtime"
DEFAULT_WORKER_SCRIPT = "scripts/sqlite_local_semantic_worker.py"


def _run(
    command: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=capture_output,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {' '.join(command)}"
            + (f"\n{completed.stderr.strip()}" if completed.stderr else "")
        )
    return completed


def _require_loopback_url(raw_url: str) -> tuple[str, int]:
    normalized = raw_url.strip()
    if not normalized.startswith(("http://", "https://")):
        normalized = f"http://{normalized}"

    parsed = urlparse(normalized)
    host = str(parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError(
            f"Local backend URLs must use loopback host (127.0.0.1 or localhost): {raw_url}"
        )

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return host, int(port)


def _tail_text_file(path: Path, max_lines: int = 40) -> str:
    if not path.exists():
        return ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = content.splitlines()
    tail = lines[-max_lines:]
    return "\n".join(tail)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False

    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            state_fields = proc_stat.read_text(encoding="utf-8", errors="replace").split()
            if len(state_fields) >= 3 and state_fields[2] == "Z":
                return False
        except OSError:
            pass

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _stop_pid(pid: int, timeout_sec: float = 8.0) -> str:
    if not _pid_is_running(pid):
        return "not_running"

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return "not_running"

    deadline = time.monotonic() + max(0.5, float(timeout_sec))
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return "stopped"
        time.sleep(0.2)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return "not_running"
    return "killed"


def _python_state_file(runtime_dir: Path) -> Path:
    return runtime_dir / "local_semantic_backend_state.json"


def _load_python_state(runtime_dir: Path) -> dict[str, object]:
    path = _python_state_file(runtime_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _write_python_state(runtime_dir: Path, payload: dict[str, object]) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = _python_state_file(runtime_dir)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _remove_python_state(runtime_dir: Path) -> None:
    path = _python_state_file(runtime_dir)
    if path.exists():
        path.unlink()


def _python_worker_command(
    *,
    worker_script: Path,
    mode: str,
    host: str,
    port: int,
    model_id: str,
    cache_dir: Path,
    service_role: str,
    request_span_log_path: Path | None,
    device: str,
) -> list[str]:
    command = [
        sys.executable,
        str(worker_script),
        "--mode",
        mode,
        "--host",
        host,
        "--port",
        str(port),
        "--model-id",
        model_id,
        "--cache-dir",
        str(cache_dir),
        "--service-role",
        service_role,
        "--device",
        str(device).strip().lower(),
    ]
    if request_span_log_path is not None:
        command.extend(["--request-span-log-path", str(request_span_log_path)])
    return command


def _python_logs(runtime_dir: Path) -> dict[str, str]:
    state = _load_python_state(runtime_dir)
    diagnostics: dict[str, str] = {}
    for role in ("embed", "rerank"):
        entry = state.get(role)
        if not isinstance(entry, dict):
            diagnostics[role] = ""
            continue
        log_path = Path(str(entry.get("log_path", "")))
        diagnostics[role] = _tail_text_file(log_path)
    return diagnostics


def _python_workers_running(runtime_dir: Path) -> bool:
    state = _load_python_state(runtime_dir)
    embed = state.get("embed")
    rerank = state.get("rerank")
    if not isinstance(embed, dict) or not isinstance(rerank, dict):
        return False
    try:
        embed_pid = int(embed.get("pid", 0))
        rerank_pid = int(rerank.get("pid", 0))
    except (TypeError, ValueError):
        return False
    return _pid_is_running(embed_pid) and _pid_is_running(rerank_pid)


def _wait_until_ready(
    *,
    embed_base_url: str,
    rerank_base_url: str,
    embed_model_id: str,
    rerank_model_id: str,
    timeout_sec: float,
    diagnostics_provider: Callable[[], dict[str, str]] | None,
    liveness_probe: Callable[[], bool] | None,
) -> dict[str, object]:
    deadline = time.monotonic() + max(1.0, float(timeout_sec))
    last_result: dict[str, object] | None = None
    while time.monotonic() <= deadline:
        if liveness_probe is not None and not liveness_probe():
            break

        result = check_semantic_backend(
            SemanticBackendConfig(
                base_url=embed_base_url,
                embed_base_url=embed_base_url,
                rerank_base_url=rerank_base_url,
                embed_model_id=embed_model_id,
                reranker_model_id=rerank_model_id,
                timeout_sec=5.0,
            )
        )
        if bool(result.get("ok", False)):
            return result
        last_result = result
        time.sleep(2.0)

    detail = json.dumps(last_result, sort_keys=True) if last_result else ""
    diagnostics = diagnostics_provider() if diagnostics_provider else {}
    raise RuntimeError(
        "Local semantic backend failed readiness checks within "
        f"{timeout_sec}s. Last result: {detail}. Diagnostics: "
        f"{json.dumps(diagnostics, sort_keys=True)}"
    )


def _stop_python_processes(runtime_dir: Path) -> dict[str, str]:
    state = _load_python_state(runtime_dir)
    actions: dict[str, str] = {}
    for role in ("embed", "rerank"):
        entry = state.get(role)
        if not isinstance(entry, dict):
            actions[role] = "not_found"
            continue
        try:
            pid = int(entry.get("pid", 0))
        except (TypeError, ValueError):
            actions[role] = "not_found"
            continue
        actions[role] = _stop_pid(pid)

    _remove_python_state(runtime_dir)
    return actions


def _start_python_processes(
    *,
    runtime_dir: Path,
    worker_script: Path,
    embed_host: str,
    embed_port: int,
    rerank_host: str,
    rerank_port: int,
    embed_model_id: str,
    rerank_model_id: str,
    model_cache_dir: Path,
    worker_span_log_path: Path | None,
    embed_device: str,
    rerank_device: str,
) -> dict[str, object]:
    if not worker_script.exists():
        raise RuntimeError(f"Python worker script does not exist: {worker_script}")

    runtime_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = runtime_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    existing = _load_python_state(runtime_dir)
    embed_existing = existing.get("embed")
    rerank_existing = existing.get("rerank")
    if (
        isinstance(embed_existing, dict)
        and isinstance(rerank_existing, dict)
        and _pid_is_running(int(embed_existing.get("pid", 0)))
        and _pid_is_running(int(rerank_existing.get("pid", 0)))
    ):
        return {
            "embed_action": "already_running",
            "rerank_action": "already_running",
        }

    _stop_python_processes(runtime_dir)

    embed_log = logs_dir / "embed.log"
    rerank_log = logs_dir / "rerank.log"

    embed_cmd = _python_worker_command(
        worker_script=worker_script,
        mode="embeddings",
        host=embed_host,
        port=embed_port,
        model_id=embed_model_id,
        cache_dir=model_cache_dir,
        service_role="embed",
        request_span_log_path=worker_span_log_path,
        device=embed_device,
    )
    rerank_cmd = _python_worker_command(
        worker_script=worker_script,
        mode="rerank",
        host=rerank_host,
        port=rerank_port,
        model_id=rerank_model_id,
        cache_dir=model_cache_dir,
        service_role="rerank",
        request_span_log_path=worker_span_log_path,
        device=rerank_device,
    )

    with embed_log.open("w", encoding="utf-8") as embed_handle:
        embed_proc = subprocess.Popen(  # noqa: S603
            embed_cmd,
            stdout=embed_handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

    try:
        with rerank_log.open("w", encoding="utf-8") as rerank_handle:
            rerank_proc = subprocess.Popen(  # noqa: S603
                rerank_cmd,
                stdout=rerank_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
    except OSError:
        _stop_pid(int(embed_proc.pid))
        raise

    _write_python_state(
        runtime_dir,
        {
            "engine": "python",
            "worker_script": str(worker_script),
            "cache_dir": str(model_cache_dir),
            "worker_span_log_path": str(worker_span_log_path) if worker_span_log_path else "",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "embed": {
                "pid": int(embed_proc.pid),
                "log_path": str(embed_log),
                "host": embed_host,
                "port": embed_port,
                "model_id": embed_model_id,
                "device": str(embed_device).strip().lower(),
            },
            "rerank": {
                "pid": int(rerank_proc.pid),
                "log_path": str(rerank_log),
                "host": rerank_host,
                "port": rerank_port,
                "model_id": rerank_model_id,
                "device": str(rerank_device).strip().lower(),
            },
        },
    )

    return {
        "embed_action": "started",
        "rerank_action": "started",
    }


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--embed-base-url",
        default=os.environ.get("RUST_REF_TEI_EMBED_BASE_URL", "http://127.0.0.1:8080"),
        help="Loopback URL for local embedding service",
    )
    parser.add_argument(
        "--rerank-base-url",
        default=os.environ.get("RUST_REF_TEI_RERANK_BASE_URL", "http://127.0.0.1:8081"),
        help="Loopback URL for local reranker service",
    )
    parser.add_argument(
        "--embed-model-id",
        default=os.environ.get("RUST_REF_EMBED_MODEL_ID", "Qwen/Qwen3-Embedding-4B"),
        help="Embedding model identifier",
    )
    parser.add_argument(
        "--rerank-model-id",
        default=os.environ.get("RUST_REF_RERANK_MODEL_ID", "BAAI/bge-reranker-v2-m3"),
        help="Reranker model identifier",
    )
    parser.add_argument(
        "--runtime-dir",
        default=os.environ.get("RUST_REF_LOCAL_BACKEND_RUNTIME_DIR", DEFAULT_RUNTIME_DIR),
        help="Runtime state directory for local backend launcher",
    )
    parser.add_argument(
        "--python-worker-script",
        default=os.environ.get("RUST_REF_LOCAL_BACKEND_WORKER_SCRIPT", DEFAULT_WORKER_SCRIPT),
        help="Python worker script path for engine=python",
    )
    parser.add_argument(
        "--worker-span-log-path",
        default=os.environ.get("RUST_REF_WORKER_SPAN_LOG_PATH", ""),
        help="Optional JSONL path for worker request span telemetry",
    )
    parser.add_argument(
        "--embed-device",
        choices=("auto", "cpu", "mps", "cuda"),
        default="auto",
        help="Device selector for embedding worker",
    )
    parser.add_argument(
        "--rerank-device",
        choices=("auto", "cpu", "mps", "cuda"),
        default="auto",
        help="Device selector for reranker worker",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage local semantic backend for retrieval commands"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start local embedding/reranker services")
    _add_common_args(start_parser)
    start_parser.add_argument(
        "--model-cache-dir",
        default=os.environ.get(
            "RUST_REF_SEMANTIC_MODEL_CACHE_DIR",
            os.environ.get("RUST_REF_TEI_MODEL_CACHE_DIR", ".cache/sqlite_kb/models/hf"),
        ),
        help="Model cache directory for local backend",
    )
    start_parser.add_argument(
        "--startup-timeout-sec",
        type=float,
        default=180.0,
        help="Max wait time for backend readiness",
    )

    stop_parser = subparsers.add_parser("stop", help="Stop local semantic services")
    _add_common_args(stop_parser)

    status_parser = subparsers.add_parser("status", help="Show local backend status")
    _add_common_args(status_parser)
    status_parser.add_argument(
        "--check-backend",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Probe health/embed/rerank endpoints in addition to runtime status",
    )
    return parser.parse_args()


def _command_start(args: argparse.Namespace) -> dict[str, object]:
    embed_host, embed_port = _require_loopback_url(str(args.embed_base_url))
    rerank_host, rerank_port = _require_loopback_url(str(args.rerank_base_url))
    if embed_port == rerank_port:
        raise RuntimeError(
            "Embedding and reranker URLs must use different ports for split local services"
        )

    cache_dir = Path(str(args.model_cache_dir)).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    runtime_dir = Path(str(args.runtime_dir)).resolve()

    diagnostics_provider: Callable[[], dict[str, str]] | None = None
    liveness_probe: Callable[[], bool] | None = None
    worker_script = Path(str(args.python_worker_script)).resolve()
    worker_span_log_path = (
        Path(str(args.worker_span_log_path)).resolve()
        if str(args.worker_span_log_path).strip()
        else None
    )
    actions = _start_python_processes(
        runtime_dir=runtime_dir,
        worker_script=worker_script,
        embed_host=embed_host,
        embed_port=embed_port,
        rerank_host=rerank_host,
        rerank_port=rerank_port,
        embed_model_id=str(args.embed_model_id),
        rerank_model_id=str(args.rerank_model_id),
        model_cache_dir=cache_dir,
        worker_span_log_path=worker_span_log_path,
        embed_device=str(args.embed_device),
        rerank_device=str(args.rerank_device),
    )

    def _python_diagnostics() -> dict[str, str]:
        return _python_logs(runtime_dir)

    diagnostics_provider = _python_diagnostics

    def _python_liveness() -> bool:
        return _python_workers_running(runtime_dir)

    liveness_probe = _python_liveness

    try:
        readiness = _wait_until_ready(
            embed_base_url=str(args.embed_base_url),
            rerank_base_url=str(args.rerank_base_url),
            embed_model_id=str(args.embed_model_id),
            rerank_model_id=str(args.rerank_model_id),
            timeout_sec=float(args.startup_timeout_sec),
            diagnostics_provider=diagnostics_provider,
            liveness_probe=liveness_probe,
        )
    except RuntimeError:
        _stop_python_processes(runtime_dir)
        raise

    return {
        "ok": True,
        "engine": "python",
        "backend_profile": "python-local",
        "embed_action": str(actions.get("embed_action", "unknown")),
        "rerank_action": str(actions.get("rerank_action", "unknown")),
        "embed_base_url": str(args.embed_base_url),
        "rerank_base_url": str(args.rerank_base_url),
        "embed_device": str(args.embed_device).strip().lower(),
        "rerank_device": str(args.rerank_device).strip().lower(),
        "runtime_dir": str(runtime_dir),
        "model_cache_dir": str(cache_dir),
        "worker_span_log_path": str(args.worker_span_log_path),
        "readiness": readiness,
    }


def _command_stop(args: argparse.Namespace) -> dict[str, object]:
    runtime_dir = Path(str(args.runtime_dir)).resolve()
    actions = _stop_python_processes(runtime_dir)

    return {
        "ok": True,
        "engine": "python",
        "actions": actions,
    }


def _command_status(args: argparse.Namespace) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "engine": "python",
        "embed_base_url": str(args.embed_base_url),
        "rerank_base_url": str(args.rerank_base_url),
        "embed_device": str(args.embed_device).strip().lower(),
        "rerank_device": str(args.rerank_device).strip().lower(),
    }
    runtime_dir = Path(str(args.runtime_dir)).resolve()
    state = _load_python_state(runtime_dir)
    processes: dict[str, dict[str, object]] = {}
    for role in ("embed", "rerank"):
        entry = state.get(role)
        if not isinstance(entry, dict):
            processes[role] = {"running": False}
            continue
        try:
            pid = int(entry.get("pid", 0))
        except (TypeError, ValueError):
            pid = 0
        processes[role] = {
            "pid": pid,
            "running": _pid_is_running(pid),
            "model_id": str(entry.get("model_id", "")),
            "log_path": str(entry.get("log_path", "")),
            "device": str(entry.get("device", "")),
        }
    payload["processes"] = processes
    payload["state_file"] = str(_python_state_file(runtime_dir))

    if bool(args.check_backend):
        payload["backend"] = check_semantic_backend(
            SemanticBackendConfig(
                base_url=str(args.embed_base_url),
                embed_base_url=str(args.embed_base_url),
                rerank_base_url=str(args.rerank_base_url),
                embed_model_id=str(args.embed_model_id),
                reranker_model_id=str(args.rerank_model_id),
            )
        )

    return payload


def main() -> int:
    args = parse_args()
    try:
        if args.command == "start":
            result = _command_start(args)
        elif args.command == "stop":
            result = _command_stop(args)
        elif args.command == "status":
            result = _command_status(args)
        else:
            raise RuntimeError(f"Unknown command: {args.command}")
    except (RuntimeError, OSError) as exc:
        print(f"[local-semantic-backend][error] {exc}")
        return EXIT_RUNTIME_FAIL

    print(json.dumps(result, indent=2, sort_keys=True))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
