from __future__ import annotations

import argparse
from pathlib import Path

from retrieval.writer_host import runtime


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Writer host runtime")
    parser.add_argument("--corpus", default="rust_reference")
    parser.add_argument("--targets", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--report-root", default="")
    parser.add_argument("--contract-path", default="config/s0/writer_prompt_contracts.yaml")
    parser.add_argument(
        "--query-testset-path",
        default="data/query_testsets/rust_reference_table1_retrieval_eval.yaml",
    )
    parser.add_argument(
        "--query-mode", choices=("lexical", "semantic", "hybrid"), default="lexical"
    )
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--model", default="")
    parser.add_argument("--agent", default="")
    parser.add_argument("--profile-path", default="")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    return runtime.run(parse_args(), root=Path(__file__).resolve().parents[3])


if __name__ == "__main__":
    raise SystemExit(main())
