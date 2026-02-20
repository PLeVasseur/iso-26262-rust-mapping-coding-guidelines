#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlite_query_guardrails import GuardrailError, execute_contract_query

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only query wrapper for rust_reference.sqlite"
    )
    parser.add_argument("--query-id", required=True, help="Contract query id to execute")
    parser.add_argument(
        "--params-json",
        default="{}",
        help="JSON object of named params passed to the contract query",
    )
    parser.add_argument(
        "--db-path",
        default=".cache/sqlite_kb/current/rust_reference.sqlite",
        help="Path to rust_reference.sqlite",
    )
    parser.add_argument(
        "--contract-path",
        default="config/sqlite_query_contracts/rust_reference.yaml",
        help="Path to rust_reference query contract YAML",
    )
    parser.add_argument(
        "--query-log-root",
        default=".cache/sqlite_kb/query_logs/rust_reference",
        help="Directory used for query audit logs",
    )
    parser.add_argument(
        "--row-limit",
        type=int,
        default=None,
        help="Optional override for row limit (guardrailed)",
    )
    return parser.parse_args()


def _parse_params(raw: str) -> dict[str, Any]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise GuardrailError("--params-json must decode to an object")
    return payload


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]

    db_path = (root / args.db_path).resolve()
    contract_path = (root / args.contract_path).resolve()
    query_log_root = (root / args.query_log_root).resolve()

    try:
        params = _parse_params(args.params_json)
        result = execute_contract_query(
            db_path=db_path,
            contract_path=contract_path,
            query_id=args.query_id,
            params=params,
            row_limit=args.row_limit,
            query_log_root=query_log_root,
        )
    except (json.JSONDecodeError, GuardrailError, OSError) as exc:
        print(f"[query-rust-reference][error] {exc}")
        return EXIT_RUNTIME_FAIL

    print(json.dumps(result, indent=2, sort_keys=True))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
