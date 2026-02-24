from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.services._invoke import run_main
from retrieval.services.capability import emit_unsupported
from sqlite_eval_retrieval import main as eval_main


def run(args: Namespace, *, root: Path) -> int:
    defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))
    if not defaults.supports_eval:
        return emit_unsupported(
            corpus=defaults.corpus,
            operation="eval",
            reason="corpus configuration disables eval",
        )

    argv = [
        "sqlite_eval_retrieval.py",
        "--corpus",
        defaults.corpus,
        "--db-path",
        str(defaults.db_path),
        "--contract-path",
        str(defaults.contract_path),
        "--query-log-root",
        str(defaults.query_log_root),
        "--eval-path",
        str(defaults.eval_path),
    ]
    profile_path = str(args.profile_path or "").strip()
    if not profile_path and defaults.profile_name:
        profile_path = f"config/retrieval_profiles/{defaults.profile_name}.yaml"
    if profile_path:
        argv.extend(["--retrieval-profile-path", profile_path])

    argv.extend(list(args.extra_args or []))
    return run_main(eval_main, argv)
