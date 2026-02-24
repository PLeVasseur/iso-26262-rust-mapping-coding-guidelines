from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.services._invoke import run_main
from retrieval.services.capability import emit_unsupported
from sqlite_migrate_schema import main as migrate_main


def run(args: Namespace, *, root: Path) -> int:
    defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))
    if not defaults.supports_migrate:
        return emit_unsupported(
            corpus=defaults.corpus,
            operation="migrate",
            reason="corpus configuration disables migrate",
        )
    argv = ["sqlite_migrate.py", "--db-path", str(defaults.db_path)]
    argv.extend(list(args.extra_args or []))
    return run_main(migrate_main, argv)
