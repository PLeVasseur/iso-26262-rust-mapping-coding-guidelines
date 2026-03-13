from __future__ import annotations

from pathlib import Path
from typing import Any

from retrieval.writer_host.fls_grounding import build_grounding_artifact


def build_resolution_packet(row: dict[str, Any], *, db_path: Path | None = None) -> dict[str, Any]:
    return build_grounding_artifact(row, db_path=db_path)
