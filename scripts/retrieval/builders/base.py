from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Protocol


class BuilderRunner(Protocol):
    def __call__(self, *, args: Namespace, root: Path) -> dict[str, object]: ...
