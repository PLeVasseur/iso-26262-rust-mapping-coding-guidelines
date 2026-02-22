#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run full nightly retrieval checks (lexical + semantic + hybrid)"
    )
    parser.add_argument(
        "--semantic-base-url",
        default=os.environ.get("RUST_REF_TEI_BASE_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument(
        "--semantic-embed-base-url",
        default=os.environ.get("RUST_REF_TEI_EMBED_BASE_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument(
        "--semantic-rerank-base-url",
        default=os.environ.get("RUST_REF_TEI_RERANK_BASE_URL", "http://127.0.0.1:8081"),
    )
    parser.add_argument(
        "--embed-model-id",
        default=os.environ.get("RUST_REF_EMBED_MODEL_ID", "Qwen/Qwen3-Embedding-4B"),
    )
    parser.add_argument(
        "--reranker-model-id",
        default=os.environ.get("RUST_REF_RERANK_MODEL_ID", "BAAI/bge-reranker-v2-m3"),
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--candidate-limit", type=int, default=5000)
    parser.add_argument(
        "--auto-start-local-backend",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--local-backend-engine",
        default=os.environ.get("RUST_REF_LOCAL_BACKEND_ENGINE", "python"),
    )
    parser.add_argument(
        "--local-backend-image",
        default="ghcr.io/huggingface/text-embeddings-inference:cpu-latest",
    )
    parser.add_argument("--local-embed-container", default="rust-ref-tei-embed")
    parser.add_argument("--local-rerank-container", default="rust-ref-tei-rerank")
    parser.add_argument(
        "--local-model-cache-dir",
        default=os.environ.get(
            "RUST_REF_SEMANTIC_MODEL_CACHE_DIR",
            os.environ.get("RUST_REF_TEI_MODEL_CACHE_DIR", ".cache/sqlite_kb/models/hf"),
        ),
    )
    parser.add_argument("--local-startup-timeout-sec", type=float, default=180.0)
    return parser.parse_args()


def _run(command: list[str], root: Path) -> None:
    completed = subprocess.run(command, cwd=root, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}")


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]

    commands = [
        ["uv", "run", "python", "scripts/sqlite_ci_retrieval_pr_fast.py"],
        [
            "uv",
            "run",
            "python",
            "scripts/sqlite_ci_retrieval_semantic.py",
            "--semantic-base-url",
            str(args.semantic_base_url),
            "--semantic-embed-base-url",
            str(args.semantic_embed_base_url),
            "--semantic-rerank-base-url",
            str(args.semantic_rerank_base_url),
            "--embed-model-id",
            str(args.embed_model_id),
            "--reranker-model-id",
            str(args.reranker_model_id),
            "--top-k",
            str(args.top_k),
            "--candidate-limit",
            str(args.candidate_limit),
            "--local-backend-engine",
            str(args.local_backend_engine),
            "--local-backend-image",
            str(args.local_backend_image),
            "--local-embed-container",
            str(args.local_embed_container),
            "--local-rerank-container",
            str(args.local_rerank_container),
            "--local-model-cache-dir",
            str(args.local_model_cache_dir),
            "--local-startup-timeout-sec",
            str(args.local_startup_timeout_sec),
        ],
    ]

    if not bool(args.auto_start_local_backend):
        commands[1].append("--no-auto-start-local-backend")

    try:
        for command in commands:
            _run(command, root=root)
    except RuntimeError as exc:
        print(f"[ci-retrieval-nightly-full][error] {exc}")
        return EXIT_RUNTIME_FAIL

    print("[ci-retrieval-nightly-full][ok] full nightly retrieval checks passed")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
