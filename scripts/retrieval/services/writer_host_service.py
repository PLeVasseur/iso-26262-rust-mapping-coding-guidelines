from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.writer_host import runtime as writer_host_runtime


def run(args: Namespace, *, root: Path) -> int:
    return int(writer_host_runtime.run(args, root=root))
