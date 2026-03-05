from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.writer_host import runtime


def run(args: Namespace, *, root: Path) -> int:
    return int(runtime.run(args, root=root))
