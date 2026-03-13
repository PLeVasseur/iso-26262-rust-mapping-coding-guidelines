from __future__ import annotations

from typing import Any


def emit_tail_reports(**kwargs: Any) -> None:
    _ = kwargs
    raise RuntimeError("Phase-A writer reports are soft-retired")
