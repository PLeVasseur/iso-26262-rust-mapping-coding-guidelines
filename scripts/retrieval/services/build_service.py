from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.services._invoke import run_main
from retrieval.services.capability import emit_unsupported
from sqlite_build import main as build_main


def run(args: Namespace, *, root: Path) -> int:
    defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))
    if not defaults.supports_build:
        return emit_unsupported(
            corpus=defaults.corpus,
            operation="build",
            reason="corpus configuration disables build",
        )
    argv = ["sqlite_build.py", "--retrieval-corpus", str(args.corpus)]
    argv.extend(list(args.extra_args or []))
    return run_main(build_main, argv)
