#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _bootstrap_import_paths() -> None:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    for candidate in (repo_root, script_dir):
        value = str(candidate)
        if value not in sys.path:
            sys.path.insert(0, value)


_bootstrap_import_paths()

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
    phase_a_retired,
    query_service,
    smoke_service,
    validate_audit_service,
    validate_service,
    verify_service,
    writer_publish_service,
    writer_evidence_service,
    writer_quality_gate_service,
    writer_review_packet_service,
    writer_run_service,
    writer_host_service,
    writer_targets_service,
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

    writer_host = subparsers.add_parser("writer-host-run")
    _add_common(writer_host)
    writer_host.add_argument("--targets", required=True)
    writer_host.add_argument("--run-id", default="")
    writer_host.add_argument("--report-root", default="")
    writer_host.add_argument("--contract-path", default="config/s0/writer_prompt_contracts.yaml")
    writer_host.add_argument(
        "--query-testset-path",
        default="data/query_testsets/rust_reference_table1_retrieval_eval.yaml",
    )
    writer_host.add_argument(
        "--query-mode",
        choices=("lexical", "semantic", "hybrid"),
        default="lexical",
    )
    writer_host.add_argument("--top-k", type=int, default=20)
    writer_host.add_argument("--max-retries", type=int, default=2)
    writer_host.add_argument("--model", default="")
    writer_host.add_argument("--agent", default="")
    writer_host.add_argument("--dry-run", action="store_true")

    writer_targets = subparsers.add_parser("writer-targets")
    _add_common(writer_targets)
    writer_targets.add_argument("--profile", choices=("fast", "full"), default="fast")
    writer_targets.add_argument("--targets", default="")
    writer_targets.add_argument(
        "--query-testset-path",
        default="data/query_testsets/rust_reference_table1_retrieval_eval.yaml",
    )
    writer_targets.add_argument("--output", default="")

    writer_run = subparsers.add_parser("writer-run")
    _add_common(writer_run)
    writer_run.add_argument("--targets", default="")
    writer_run.add_argument("--run-id", default="")
    writer_run.add_argument("--report-root", default="")
    writer_run.add_argument("--evidence-manifest", default="")
    writer_run.add_argument("--contract-path", default="config/s0/writer_prompt_contracts.yaml")
    writer_run.add_argument(
        "--query-testset-path",
        default="data/query_testsets/rust_reference_table1_retrieval_eval.yaml",
    )
    writer_run.add_argument(
        "--query-mode", choices=("lexical", "semantic", "hybrid"), default="lexical"
    )
    writer_run.add_argument("--top-k", type=int, default=20)
    writer_run.add_argument("--max-retries", type=int, default=2)
    writer_run.add_argument("--model", default="")
    writer_run.add_argument("--agent", default="")
    writer_run.add_argument("--dry-run", action="store_true")

    writer_evidence = subparsers.add_parser("writer-evidence")
    _add_common(writer_evidence)
    writer_evidence.add_argument("--targets", required=True)
    writer_evidence.add_argument("--run-id", default="")
    writer_evidence.add_argument("--report-root", default="")
    writer_evidence.add_argument("--output", default="")
    writer_evidence.add_argument("--modes", default="lexical,semantic,hybrid")
    writer_evidence.add_argument("--top-k", type=int, default=20)
    writer_evidence.add_argument("--top-n", type=int, default=8)
    writer_evidence.add_argument("--rrf-k", type=int, default=60)
    writer_evidence.add_argument("--rank-window", type=int, default=100)
    writer_evidence.add_argument("--allow-degraded", action="store_true")
    writer_evidence.add_argument(
        "--query-testset-path",
        default="data/query_testsets/rust_reference_table1_retrieval_eval.yaml",
    )

    writer_quality_gate = subparsers.add_parser("writer-quality-gate")
    _add_common(writer_quality_gate)
    writer_quality_gate.add_argument("--run-dir", required=True)
    writer_quality_gate.add_argument("--output", default="")

    writer_review_packet = subparsers.add_parser("writer-review-packet")
    _add_common(writer_review_packet)
    writer_review_packet.add_argument("--run-dir", required=True)
    writer_review_packet.add_argument("--output", default="")

    writer_publish = subparsers.add_parser("writer-publish")
    _add_common(writer_publish)
    writer_publish.add_argument(
        "--mode", choices=("publishable", "exploratory"), default="publishable"
    )
    writer_publish.add_argument("--profile", choices=("fast", "full"), default="fast")
    writer_publish.add_argument("--dry-run", action="store_true")
    writer_publish.add_argument("--output", default="")

    scaffold = subparsers.add_parser("scaffold-s0-config")
    scaffold.add_argument("--corpus-set", choices=("s0",), default="s0")
    scaffold.add_argument("--overwrite", action="store_true")
    scaffold.add_argument("--run-id", default="")
    scaffold.add_argument("--report-root", default="")

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--corpus-set", choices=("s0",), default="s0")
    doctor.add_argument(
        "--mode", choices=("bootstrap", "publishable", "exploratory"), default="bootstrap"
    )
    doctor.add_argument("--scope", choices=("drafting", "ingest", "all"), default="drafting")
    doctor.add_argument("--run-id", default="")
    doctor.add_argument("--report-root", default="")

    enumerate_targets = subparsers.add_parser("enumerate-targets")
    enumerate_targets.add_argument("--corpus-set", choices=("s0",), default="s0")
    enumerate_targets.add_argument("--profile", choices=("fast", "full"), default="full")
    enumerate_targets.add_argument(
        "--mode", choices=("bootstrap", "publishable", "exploratory"), default="bootstrap"
    )
    enumerate_targets.add_argument("--run-id", default="")
    enumerate_targets.add_argument("--report-root", default="")

    calibration = subparsers.add_parser("calibration-run")
    calibration.add_argument("--corpus-set", choices=("s0",), default="s0")
    calibration.add_argument("--profile", choices=("fast", "full"), default="full")
    calibration.add_argument(
        "--mode", choices=("bootstrap", "publishable", "exploratory"), default="bootstrap"
    )
    calibration.add_argument("--run-id", default="")
    calibration.add_argument("--report-root", default="")
    calibration.add_argument("--no-reuse-existing", action="store_true")
    calibration.add_argument("--resume", action="store_true")

    enforce = subparsers.add_parser("enforce-calibration-quality")
    enforce.add_argument("--run-id", required=True)
    enforce.add_argument(
        "--mode", choices=("bootstrap", "publishable", "exploratory"), default="bootstrap"
    )
    enforce.add_argument("--report-root", default="")

    packet = subparsers.add_parser("pack-reviewer-packet")
    packet.add_argument("--kind", choices=("calibration", "pilot", "publishable"), required=True)
    packet.add_argument("--run-id", required=True)
    packet.add_argument("--report-root", default="")

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

        if args.subcommand == "scaffold-s0-config":
            return phase_a_retired.run_scaffold_s0_config(args, root=root)
        if args.subcommand == "doctor":
            return phase_a_retired.run_doctor(args, root=root)
        if args.subcommand == "enumerate-targets":
            return phase_a_retired.run_enumerate_targets(args, root=root)
        if args.subcommand == "calibration-run":
            return phase_a_retired.run_calibration_run(args, root=root)
        if args.subcommand == "enforce-calibration-quality":
            return phase_a_retired.run_enforce_calibration_quality(args, root=root)
        if args.subcommand == "pack-reviewer-packet":
            return phase_a_retired.run_pack_reviewer_packet(args, root=root)

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
            "writer-host-run": defaults.supports_query,
            "writer-targets": defaults.supports_query,
            "writer-evidence": defaults.supports_query,
            "writer-run": defaults.supports_query,
            "writer-quality-gate": defaults.supports_query,
            "writer-review-packet": defaults.supports_query,
            "writer-publish": defaults.supports_query,
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
        if args.subcommand == "writer-host-run":
            return writer_host_service.run(args, root=root)
        if args.subcommand == "writer-targets":
            return writer_targets_service.run(args, root=root)
        if args.subcommand == "writer-run":
            return writer_run_service.run(args, root=root)
        if args.subcommand == "writer-evidence":
            return writer_evidence_service.run(args, root=root)
        if args.subcommand == "writer-quality-gate":
            return writer_quality_gate_service.run(args, root=root)
        if args.subcommand == "writer-review-packet":
            return writer_review_packet_service.run(args, root=root)
        if args.subcommand == "writer-publish":
            return writer_publish_service.run(args, root=root)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"[sqlite_kb][error] {exc}")
        return EXIT_RUNTIME_FAIL

    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
