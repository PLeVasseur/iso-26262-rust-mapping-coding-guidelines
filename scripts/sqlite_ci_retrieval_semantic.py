#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run semantic/hybrid retrieval CI lane checks")
    parser.add_argument("--corpus", default="rust_reference")
    parser.add_argument(
        "--semantic-base-url",
        default="http://127.0.0.1:8080",
    )
    parser.add_argument(
        "--semantic-embed-base-url",
        default="http://127.0.0.1:8080",
    )
    parser.add_argument(
        "--semantic-rerank-base-url",
        default="http://127.0.0.1:8081",
    )
    parser.add_argument("--embed-model-id", default="Qwen/Qwen3-Embedding-4B")
    parser.add_argument("--reranker-model-id", default="BAAI/bge-reranker-v2-m3")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--candidate-limit", type=int, default=5000)
    parser.add_argument(
        "--auto-start-local-backend",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Start local backend when semantic preflight is unavailable",
    )
    parser.add_argument(
        "--keep-local-backend-running",
        action="store_true",
        help="Do not stop locally-started backend after checks complete",
    )
    parser.add_argument(
        "--local-embed-device",
        choices=("auto", "cpu", "mps", "cuda"),
        default="auto",
    )
    parser.add_argument(
        "--local-rerank-device",
        choices=("auto", "cpu", "mps", "cuda"),
        default="auto",
    )
    parser.add_argument(
        "--local-model-cache-dir",
        default=".cache/sqlite_kb/models/hf",
    )
    parser.add_argument("--local-startup-timeout-sec", type=float, default=180.0)
    return parser.parse_args()


def _run(command: list[str], root: Path, *, check: bool = True) -> int:
    completed = subprocess.run(command, cwd=root, check=False)
    if check and completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}")
    return int(completed.returncode)


def _resolve_urls(args: argparse.Namespace) -> tuple[str, str, str]:
    base_url = str(args.semantic_base_url).strip()
    embed_url = str(args.semantic_embed_base_url).strip() or base_url
    rerank_url = str(args.semantic_rerank_base_url).strip() or base_url
    return base_url, embed_url, rerank_url


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    base_url, embed_url, rerank_url = _resolve_urls(args)

    preflight_command = [
        "uv",
        "run",
        "python",
        "scripts/sqlite_check_semantic_backend.py",
        "--base-url",
        base_url,
        "--embed-base-url",
        embed_url,
        "--rerank-base-url",
        rerank_url,
        "--embed-model",
        str(args.embed_model_id),
        "--rerank-model",
        str(args.reranker_model_id),
    ]

    started_local_backend = False
    if bool(args.auto_start_local_backend):
        preflight_rc = _run(preflight_command, root=root, check=False)
        if preflight_rc != 0:
            start_command = [
                "uv",
                "run",
                "python",
                "scripts/sqlite_local_semantic_backend.py",
                "start",
                "--embed-base-url",
                embed_url,
                "--rerank-base-url",
                rerank_url,
                "--embed-model-id",
                str(args.embed_model_id),
                "--rerank-model-id",
                str(args.reranker_model_id),
                "--embed-device",
                str(args.local_embed_device),
                "--rerank-device",
                str(args.local_rerank_device),
                "--model-cache-dir",
                str(args.local_model_cache_dir),
                "--startup-timeout-sec",
                str(args.local_startup_timeout_sec),
            ]
            _run(start_command, root=root)
            started_local_backend = True

    commands = [
        preflight_command,
        [
            "uv",
            "run",
            "python",
            "scripts/sqlite_kb.py",
            "materialize",
            "--corpus",
            str(args.corpus),
            "--semantic-base-url",
            base_url,
            "--semantic-embed-base-url",
            embed_url,
            "--semantic-rerank-base-url",
            rerank_url,
            "--embed-model-id",
            str(args.embed_model_id),
            "--reranker-model-id",
            str(args.reranker_model_id),
            "--semantic-retries",
            "0",
        ],
        [
            "uv",
            "run",
            "python",
            "scripts/sqlite_kb.py",
            "eval",
            "--corpus",
            str(args.corpus),
            "--semantic-base-url",
            base_url,
            "--semantic-embed-base-url",
            embed_url,
            "--semantic-rerank-base-url",
            rerank_url,
            "--embed-model-id",
            str(args.embed_model_id),
            "--reranker-model-id",
            str(args.reranker_model_id),
            "--top-k",
            str(args.top_k),
            "--candidate-limit",
            str(args.candidate_limit),
            "--semantic-retries",
            "0",
        ],
    ]

    try:
        for command in commands:
            _run(command, root=root)
    except RuntimeError as exc:
        print(f"[ci-retrieval-semantic][error] {exc}")
        return EXIT_RUNTIME_FAIL
    finally:
        if started_local_backend and not bool(args.keep_local_backend_running):
            _run(
                [
                    "uv",
                    "run",
                    "python",
                    "scripts/sqlite_local_semantic_backend.py",
                    "stop",
                ],
                root=root,
                check=False,
            )

    print("[ci-retrieval-semantic][ok] semantic/hybrid retrieval checks passed")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
