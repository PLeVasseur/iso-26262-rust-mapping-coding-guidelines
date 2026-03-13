from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.services._invoke import run_main
from retrieval.services.capability import emit_unsupported
from sqlite_validate import main as validate_main


def run(args: Namespace, *, root: Path) -> int:
    defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))
    if not defaults.supports_validate:
        return emit_unsupported(
            corpus=defaults.corpus,
            operation="validate",
            reason="corpus configuration disables validate",
        )
    argv = ["sqlite_validate.py", "--db-path", str(defaults.db_path)]
    argv.extend(list(args.extra_args or []))
    return run_main(validate_main, argv)
