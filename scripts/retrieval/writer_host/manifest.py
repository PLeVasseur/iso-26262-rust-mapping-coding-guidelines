from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("writer evidence manifest must be an object")
    targets = payload.get("targets")
    if not isinstance(targets, list) or not targets:
        raise RuntimeError("writer evidence manifest missing targets")
    return payload


def target_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = manifest.get("targets")
    if not isinstance(rows, list):
        return {}
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        target_id = str(row.get("target_id", "")).strip()
        if not target_id:
            continue
        index[target_id] = row
    return index
