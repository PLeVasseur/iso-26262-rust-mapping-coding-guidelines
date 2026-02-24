from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.services._invoke import run_main
from retrieval.services.capability import emit_unsupported
from sqlite_query import main as query_main


def run(args: Namespace, *, root: Path) -> int:
    defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))
    if not defaults.supports_query:
        return emit_unsupported(
            corpus=defaults.corpus,
            operation="query",
            reason="corpus configuration disables query",
        )

    argv = [
        "sqlite_query.py",
        "--corpus",
        defaults.corpus,
        "--db-path",
        str(defaults.db_path),
        "--contract-path",
        str(defaults.contract_path),
        "--query-log-root",
        str(defaults.query_log_root),
        "--rewrite-rules-path",
        str(defaults.rewrite_rules_path),
    ]
    profile_path = str(args.profile_path or "").strip()
    if not profile_path and defaults.profile_name:
        profile_path = f"config/retrieval_profiles/{defaults.profile_name}.yaml"
    if profile_path:
        argv.extend(["--retrieval-profile-path", profile_path])

    argv.extend(list(args.extra_args or []))
    return run_main(query_main, argv)
