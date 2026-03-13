#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys

from semantic_backend_client import SemanticBackendConfig, check_semantic_backend

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preflight check for local semantic embedding/reranker backend"
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("RUST_REF_TEI_BASE_URL", "http://127.0.0.1:8080"),
        help="Fallback semantic backend base URL",
    )
    parser.add_argument(
        "--embed-base-url",
        default=os.environ.get("RUST_REF_TEI_EMBED_BASE_URL", "http://127.0.0.1:8080"),
        help="Optional embedding backend base URL override",
    )
    parser.add_argument(
        "--rerank-base-url",
        default=os.environ.get("RUST_REF_TEI_RERANK_BASE_URL", "http://127.0.0.1:8081"),
        help="Optional reranker backend base URL override",
    )
    parser.add_argument(
        "--embed-model",
        default=os.environ.get("RUST_REF_EMBED_MODEL_ID", "Qwen/Qwen3-Embedding-4B"),
        help="Embedding model identifier",
    )
    parser.add_argument(
        "--rerank-model",
        default=os.environ.get("RUST_REF_RERANK_MODEL_ID", "BAAI/bge-reranker-v2-m3"),
        help="Reranker model identifier",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=10.0,
        help="HTTP timeout per backend call",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = SemanticBackendConfig(
        base_url=args.base_url,
        embed_model_id=args.embed_model,
        reranker_model_id=args.rerank_model,
        timeout_sec=float(args.timeout_sec),
        embed_base_url=(str(args.embed_base_url).strip() or None),
        rerank_base_url=(str(args.rerank_base_url).strip() or None),
    )
    result = check_semantic_backend(config)
    print(json.dumps(result, indent=2, sort_keys=True))
    return EXIT_SUCCESS if bool(result.get("ok", False)) else EXIT_RUNTIME_FAIL


if __name__ == "__main__":
    sys.exit(main())
