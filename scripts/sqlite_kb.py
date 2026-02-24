#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.corpora.registry import list_supported_corpora
from retrieval.services import (
    build_service,
    capture_service,
    eval_service,
    materialize_service,
    migrate_service,
    query_service,
    smoke_service,
    validate_service,
    verify_service,
)
from retrieval.services.provenance_guard import enforce_provenance_guard

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--corpus", choices=list_supported_corpora(), required=True)
    parser.add_argument(
        "--profile-path",
        default="",
        help="Optional retrieval profile path override",
    )
    parser.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Additional arguments forwarded to the selected subcommand",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unified sqlite_kb multi-corpus command")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    for name in (
        "query",
        "eval",
        "build",
        "materialize",
        "smoke",
        "capture",
        "verify",
        "validate",
        "migrate",
    ):
        subparser = subparsers.add_parser(name)
        _add_common(subparser)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]

    extras = list(getattr(args, "extra_args", []) or [])
    if extras and extras[0] == "--":
        extras = extras[1:]
    args.extra_args = extras

    try:
        defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))
        support_map = {
            "query": defaults.supports_query,
            "eval": defaults.supports_eval,
            "build": defaults.supports_build,
            "materialize": defaults.supports_materialize,
            "smoke": defaults.supports_smoke,
            "capture": defaults.supports_capture,
            "verify": defaults.supports_verify,
            "validate": defaults.supports_validate,
            "migrate": defaults.supports_migrate,
        }
        if bool(support_map.get(str(args.subcommand), True)):
            enforce_provenance_guard(
                root=root,
                operation=str(args.subcommand),
                corpus=defaults.corpus,
                default_db_path=defaults.db_path,
                default_profile_name=defaults.profile_name,
                default_eval_policy_id=defaults.eval_policy_path.stem,
                default_ingest_strategy=defaults.ingest_strategy,
                chunk_target_min_tokens=defaults.chunk_target_min_tokens,
                chunk_target_max_tokens=defaults.chunk_target_max_tokens,
                extra_args=list(args.extra_args or []),
            )

        if args.subcommand == "query":
            return query_service.run(args, root=root)
        if args.subcommand == "eval":
            return eval_service.run(args, root=root)
        if args.subcommand == "build":
            return build_service.run(args, root=root)
        if args.subcommand == "materialize":
            return materialize_service.run(args, root=root)
        if args.subcommand == "smoke":
            return smoke_service.run(args, root=root)
        if args.subcommand == "capture":
            return capture_service.run(args, root=root)
        if args.subcommand == "verify":
            return verify_service.run(args, root=root)
        if args.subcommand == "validate":
            return validate_service.run(args, root=root)
        if args.subcommand == "migrate":
            return migrate_service.run(args, root=root)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"[sqlite_kb][error] {exc}")
        return EXIT_RUNTIME_FAIL

    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
