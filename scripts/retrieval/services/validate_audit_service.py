from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.services._invoke import run_main
from sqlite_validate_subagent_audit import main as validate_audit_main


def run(args: Namespace, *, root: Path) -> int:
    del root
    argv = ["sqlite_validate_subagent_audit.py"]
    argv.extend(list(args.extra_args or []))
    return run_main(validate_audit_main, argv)
