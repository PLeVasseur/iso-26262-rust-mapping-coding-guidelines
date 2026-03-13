from __future__ import annotations

import sys
from collections.abc import Callable
from contextlib import contextmanager


@contextmanager
def _argv(argv: list[str]):
    original = list(sys.argv)
    sys.argv = argv
    try:
        yield
    finally:
        sys.argv = original


def run_main(main_fn: Callable[[], int], argv: list[str]) -> int:
    with _argv(argv):
        return int(main_fn())
