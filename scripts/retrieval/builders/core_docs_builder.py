from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path


def run_core_docs_build(*, args: Namespace, root: Path) -> dict[str, object]:
    payload = {
        "corpus": "core_docs",
        "db_path": str((root / args.db_path).resolve()),
    }
    raise RuntimeError(
        "core_docs build requires rustdoc-json ingestion pipeline and is not enabled in this "
        "phase. "
        "Use 'sqlite_kb.py query/eval --corpus core_docs' for phase-A operations. "
        f"payload={json.dumps(payload, sort_keys=True)}"
    )
