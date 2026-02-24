from __future__ import annotations

from argparse import Namespace
from pathlib import Path


def run_core_docs_build(*, args: Namespace, root: Path) -> dict[str, object]:
    _ = (args, root)
    raise RuntimeError(
        "core_docs build is blocked until rustdoc item-level extraction is implemented and "
        "approved via P0 chunking strategy signoff"
    )
