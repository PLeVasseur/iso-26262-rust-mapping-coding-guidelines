from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.writer_host import evidence


def run(args: Namespace, *, root: Path) -> int:
    return int(evidence.run(args, root=root))
