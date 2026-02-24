from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.services._invoke import run_main
from retrieval.services.capability import emit_unsupported
from sqlite_verify_rust_reference_query_set import main as verify_main


def run(args: Namespace, *, root: Path) -> int:
    defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))
    if not defaults.supports_verify:
        return emit_unsupported(
            corpus=defaults.corpus,
            operation="verify",
            reason="corpus configuration disables verify",
        )
    argv = ["sqlite_verify.py"]
    argv.extend(list(args.extra_args or []))
    return run_main(verify_main, argv)
