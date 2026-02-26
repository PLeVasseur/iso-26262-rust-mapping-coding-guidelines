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
    eval_report_service,
    eval_service,
    export_service,
    guidelines_repo_service,
    inspect_service,
    materialize_service,
    migrate_service,
    query_service,
    smoke_service,
    validate_audit_service,
    validate_service,
    verify_service,
)
from retrieval.services.capability import emit_unsupported
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
    if len(sys.argv) > 1 and sys.argv[1] == "guidelines-repo":
        parser = argparse.ArgumentParser(description="Guidelines repo operations")
        parser.add_argument("command_family", choices=("guidelines-repo",))
        subparsers = parser.add_subparsers(dest="guidelines_subcommand", required=True)

        doctor = subparsers.add_parser("doctor")
        doctor.add_argument(
            "--mode",
            choices=("publishable", "exploratory"),
            default="publishable",
        )

        ensure_repo = subparsers.add_parser("ensure-repo")
        ensure_repo.add_argument(
            "--mode",
            choices=("publishable", "exploratory"),
            default="publishable",
        )
        ensure_repo.add_argument("--allow-main", action="store_true")

        bootstrap = subparsers.add_parser("bootstrap-guidelines-repo")
        bootstrap_group = bootstrap.add_mutually_exclusive_group(required=True)
        bootstrap_group.add_argument("--verify", action="store_true")
        bootstrap_group.add_argument("--apply", action="store_true")
        bootstrap.add_argument("--no-commit", action="store_true")
        bootstrap.add_argument("--allow-dirty-bootstrap", action="store_true")

        bump_pin = subparsers.add_parser("bump-pin")
        bump_pin.add_argument("--revision", required=True)

        reorg = subparsers.add_parser("reorg-path-mapping")
        reorg.add_argument("--mapping-file", default="")

        autopilot = subparsers.add_parser("autopilot")
        autopilot.add_argument("--profile", choices=("fast", "full"), required=True)
        autopilot.add_argument(
            "--mode",
            choices=("publishable", "exploratory"),
            default="publishable",
        )
        autopilot.add_argument("--allow-main", action="store_true")
        return parser.parse_args()

    parser = argparse.ArgumentParser(description="Unified sqlite_kb multi-corpus command")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    for name in (
        "inspect",
        "query",
        "eval",
        "eval-report",
        "export-rst",
        "build",
        "materialize",
        "smoke",
        "capture",
        "verify",
        "validate",
        "migrate",
        "validate-audit",
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
        if getattr(args, "command_family", "") == "guidelines-repo":
            if str(args.guidelines_subcommand) == "doctor":
                return guidelines_repo_service.run_doctor(args, root=root)
            if str(args.guidelines_subcommand) == "ensure-repo":
                return guidelines_repo_service.run_ensure_repo(args, root=root)
            if str(args.guidelines_subcommand) == "bootstrap-guidelines-repo":
                return guidelines_repo_service.run_bootstrap_guidelines_repo(args, root=root)
            if str(args.guidelines_subcommand) == "bump-pin":
                return guidelines_repo_service.run_bump_pin(args, root=root)
            if str(args.guidelines_subcommand) == "reorg-path-mapping":
                return guidelines_repo_service.run_reorg_path_mapping(args, root=root)
            if str(args.guidelines_subcommand) == "autopilot":
                return guidelines_repo_service.run_autopilot(args, root=root)
            raise RuntimeError(
                f"Unsupported guidelines-repo operation: {args.guidelines_subcommand}"
            )

        defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))
        support_map = {
            "inspect": defaults.supports_inspect,
            "query": defaults.supports_query,
            "eval": defaults.supports_eval,
            "eval-report": defaults.supports_eval,
            "export-rst": defaults.supports_build,
            "build": defaults.supports_build,
            "materialize": defaults.supports_materialize,
            "smoke": defaults.supports_smoke,
            "capture": defaults.supports_capture,
            "verify": defaults.supports_verify,
            "validate": defaults.supports_validate,
            "migrate": defaults.supports_migrate,
            "validate-audit": defaults.supports_eval,
        }
        if not bool(support_map.get(str(args.subcommand), True)):
            return emit_unsupported(
                corpus=defaults.corpus,
                operation=str(args.subcommand),
                reason=f"corpus configuration disables {args.subcommand}",
            )
        if bool(support_map.get(str(args.subcommand), True)) and str(args.subcommand) != "inspect":
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
                chunk_overlap_percent=defaults.chunk_overlap_percent,
                extra_args=list(args.extra_args or []),
            )

        if args.subcommand == "inspect":
            return inspect_service.run(args, root=root)
        if args.subcommand == "query":
            return query_service.run(args, root=root)
        if args.subcommand == "eval":
            return eval_service.run(args, root=root)
        if args.subcommand == "eval-report":
            return eval_report_service.run(args, root=root)
        if args.subcommand == "export-rst":
            return export_service.run(args, root=root)
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
        if args.subcommand == "validate-audit":
            return validate_audit_service.run(args, root=root)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"[sqlite_kb][error] {exc}")
        return EXIT_RUNTIME_FAIL

    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
