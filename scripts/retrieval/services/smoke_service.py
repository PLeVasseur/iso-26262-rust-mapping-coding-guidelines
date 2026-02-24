from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.services._invoke import run_main
from retrieval.services.capability import emit_unsupported
from sqlite_smoke import main as smoke_main


def run(args: Namespace, *, root: Path) -> int:
    defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))
    if not defaults.supports_smoke:
        return emit_unsupported(
            corpus=defaults.corpus,
            operation="smoke",
            reason="corpus configuration disables smoke",
        )
    smoke_contract = root / "config" / "sqlite_query_contracts" / f"{defaults.corpus}.yaml"
    contract_path = smoke_contract if smoke_contract.exists() else defaults.contract_path

    argv = [
        "sqlite_smoke.py",
        "--db-path",
        str(defaults.db_path),
        "--contract-path",
        str(contract_path),
        "--query-log-root",
        str(defaults.query_log_root),
    ]
    argv.extend(list(args.extra_args or []))
    return run_main(smoke_main, argv)
