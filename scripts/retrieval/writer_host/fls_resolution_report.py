from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return cleaned or "unknown"


def write_resolution_report(
    *,
    report_root: Path,
    target_id: str,
    title: str,
    payload: dict[str, Any],
) -> Path:
    report_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    file_name = f"{_slug(target_id)}_{stamp}.json"
    path = report_root / file_name
    out = {
        "target_id": target_id,
        "title": title,
        **payload,
    }
    path.write_text(json.dumps(out, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path
