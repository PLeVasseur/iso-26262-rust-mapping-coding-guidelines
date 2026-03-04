from __future__ import annotations

import argparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize statement embeddings into rust_reference.sqlite"
    )
    parser.add_argument(
        "--db-path",
        default=".cache/sqlite_kb/current/rust_reference.sqlite",
        help="Path to rust_reference sqlite file",
    )
    parser.add_argument(
        "--contract-path",
        default="config/sqlite_query_contracts/rust_reference_chunk.yaml",
        help="Path to rust_reference query contract",
    )
    parser.add_argument(
        "--query-log-root",
        default=".cache/sqlite_kb/query_logs/rust_reference",
        help="Directory used for query audit logs",
    )
    parser.add_argument("--row-marker", default="", help="Optional Table 1 row marker filter")
    parser.add_argument("--batch-size", type=int, default=32, help="Embedding batch size")
    parser.add_argument(
        "--semantic-base-url",
        default="http://127.0.0.1:8080",
        help="Fallback semantic backend base URL",
    )
    parser.add_argument(
        "--semantic-embed-base-url",
        default="http://127.0.0.1:8080",
        help="Optional embedding backend base URL override",
    )
    parser.add_argument(
        "--semantic-rerank-base-url",
        default="http://127.0.0.1:8081",
        help="Optional reranker backend base URL override",
    )
    parser.add_argument(
        "--embed-model-id",
        default="Qwen/Qwen3-Embedding-4B",
        help="Embedding model identifier",
    )
    parser.add_argument(
        "--reranker-model-id",
        default="BAAI/bge-reranker-v2-m3",
        help="Reranker model identifier metadata",
    )
    parser.add_argument(
        "--semantic-timeout-sec",
        type=float,
        default=60.0,
        help="Semantic backend timeout per HTTP call",
    )
    parser.add_argument(
        "--semantic-retries",
        type=int,
        default=0,
        help="Retry count for embedding calls",
    )
    parser.add_argument(
        "--require-mps",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Require effective embed backend device to be mps (fail-closed when enabled)",
    )
    parser.add_argument(
        "--allow-cpu-fallback",
        action="store_true",
        help="Allow CPU fallback with loud warning payload",
    )
    parser.add_argument(
        "--allow-partial-corpus",
        action="store_true",
        help=(
            "Allow scoped/partial corpus materialization without full-corpus parity checks "
            "(for local experimentation)"
        ),
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=0,
        help="Optional cap on corpus rows for calibration sweeps (0 disables cap)",
    )
    parser.add_argument(
        "--progress-log-path",
        default="",
        help=(
            "Path to JSONL progress log file. Defaults to "
            ".cache/sqlite_kb/reports/rust_reference/materialize_progress_<UTC>.jsonl"
        ),
    )
    parser.add_argument(
        "--progress-interval-sec",
        type=float,
        default=60.0,
        help="Minimum seconds between progress events (final batch always logs)",
    )
    parser.add_argument(
        "--allow-provenance-mismatch",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()
