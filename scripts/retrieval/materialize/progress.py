from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def resolve_progress_log_path(root: Path, raw_path: str) -> Path:
    candidate = str(raw_path).strip()
    if candidate:
        path = Path(candidate)
        if not path.is_absolute():
            path = (root / path).resolve()
        return path
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return (
        root
        / ".cache"
        / "sqlite_kb"
        / "reports"
        / "rust_reference"
        / f"materialize_progress_{stamp}.jsonl"
    ).resolve()


def append_progress_event(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True))
        handle.write("\n")


def progress_metrics(
    *,
    started_monotonic: float,
    created: int,
    baseline_cached: int,
    target_rows: int,
) -> dict[str, Any]:
    elapsed_sec = max(0.001, float(time.perf_counter() - started_monotonic))
    cached_now = int(baseline_cached + created)
    remaining = max(0, int(target_rows - cached_now))
    rows_per_min = (float(created) * 60.0) / elapsed_sec
    eta_min: float | None = None
    if rows_per_min > 0.0:
        eta_min = float(remaining) / rows_per_min
    return {
        "elapsed_sec": round(elapsed_sec, 3),
        "cached_now": cached_now,
        "remaining": remaining,
        "rows_per_min": round(rows_per_min, 3),
        "eta_min": None if eta_min is None else round(eta_min, 3),
    }
