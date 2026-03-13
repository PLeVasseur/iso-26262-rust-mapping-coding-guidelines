from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any


def run_rust_reference_build(*, args: Namespace, root: Path) -> dict[str, Any]:
    from retrieval.operations import build as build_operation

    return build_operation.run_rust_reference_build(args=args, root=root)
