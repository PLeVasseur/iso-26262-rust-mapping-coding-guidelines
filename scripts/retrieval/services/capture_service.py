from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.services._invoke import run_main
from retrieval.services.capability import emit_unsupported
from sqlite_capture_query_reviews import main as capture_main


def run(args: Namespace, *, root: Path) -> int:
    defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))
    if not defaults.supports_capture:
        return emit_unsupported(
            corpus=defaults.corpus,
            operation="capture",
            reason="corpus configuration disables capture",
        )
    argv = [
        "sqlite_capture.py",
        "--db-path",
        str(defaults.db_path),
        "--contract-path",
        str(defaults.contract_path),
        "--query-log-root",
        str(defaults.query_log_root),
        "--prompts-path",
        str(defaults.eval_path),
    ]
    argv.extend(list(args.extra_args or []))
    return run_main(capture_main, argv)
