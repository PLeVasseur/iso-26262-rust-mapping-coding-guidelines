from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.services._invoke import run_main
from sqlite_eval_human_report import main as eval_report_main


def run(args: Namespace, *, root: Path) -> int:
    defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))

    argv = [
        "sqlite_eval_human_report.py",
        "--corpus",
        defaults.corpus,
        "--db-path",
        str(defaults.db_path),
        "--testset-path",
        str(defaults.eval_path),
        "--report-root",
        str(defaults.report_root),
    ]
    argv.extend(list(args.extra_args or []))
    return run_main(eval_report_main, argv)
