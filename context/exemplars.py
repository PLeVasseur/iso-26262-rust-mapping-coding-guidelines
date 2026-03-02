"""Helpers for locating curated exemplar guideline files."""

from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXEMPLAR_MANIFEST = PROJECT_ROOT / "data" / "exemplar_manifest.json"
GUIDELINES_REPO_ROOT = Path(
    os.environ.get(
        "GUIDELINES_REPO", "/Users/pete.levasseur/personal/safety-critical-rust-coding-guidelines"
    )
)


def get_exemplar_paths(
    manifest_path: Path = EXEMPLAR_MANIFEST,
    guidelines_repo_root: Path = GUIDELINES_REPO_ROOT,
) -> list[Path]:
    """Return absolute paths to curated exemplar RST files."""
    if not manifest_path.exists():
        return []
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = payload.get("exemplars") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []

    paths: list[Path] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_path = str(row.get("path", "")).strip()
        if not raw_path:
            continue
        path = guidelines_repo_root / raw_path
        if path.exists():
            paths.append(path)
    return paths
