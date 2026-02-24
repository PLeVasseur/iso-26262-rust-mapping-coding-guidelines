from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.services._invoke import run_main
from retrieval.services.capability import emit_unsupported
from sqlite_materialize_rust_reference_embeddings import main as materialize_main


def run(args: Namespace, *, root: Path) -> int:
    defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))
    if not defaults.supports_materialize:
        return emit_unsupported(
            corpus=defaults.corpus,
            operation="materialize",
            reason="corpus configuration disables materialize",
        )
    argv = [
        "sqlite_materialize_embeddings.py",
        "--db-path",
        str(defaults.db_path),
        "--contract-path",
        str(defaults.contract_path),
        "--query-log-root",
        str(defaults.query_log_root),
    ]
    argv.extend(list(args.extra_args or []))
    return run_main(materialize_main, argv)
