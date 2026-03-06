from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


def _now_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def load_prompts(path: Path) -> list[dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    prompts = payload.get("prompts") if isinstance(payload, dict) else None
    if not isinstance(prompts, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in prompts:
        if isinstance(row, dict) and str(row.get("prompt_id", "")).strip():
            rows.append(row)
    return rows


def load_targets_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    targets = payload.get("targets") if isinstance(payload, dict) else []
    if not isinstance(targets, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in targets:
        if not isinstance(row, dict):
            continue
        prompt_id = str(row.get("prompt_id", "")).strip()
        query_text = str(row.get("query_text", "")).strip()
        if not prompt_id or not query_text:
            continue
        rows.append(
            {
                "prompt_id": prompt_id,
                "query_text": query_text,
                "expected_row_markers": list(row.get("expected_row_markers") or []),
            }
        )
    return rows


def _pick_broad_batch(prompts: list[dict[str, Any]]) -> list[str]:
    by_marker: dict[str, str] = {}
    for row in prompts:
        prompt_id = str(row.get("prompt_id", "")).strip()
        markers = list(row.get("expected_row_markers") or [])
        for marker in markers:
            key = str(marker).strip()
            if key and key not in by_marker:
                by_marker[key] = prompt_id
    selected = [by_marker[key] for key in sorted(by_marker.keys()) if key]
    for must in ("RET-ISSUE-005", "RET-RESOLVE-008"):
        if must not in selected and any(
            str(p.get("prompt_id", "")).strip() == must for p in prompts
        ):
            selected.append(must)
    return selected


def build_manifest(
    *, prompts: list[dict[str, Any]], profile: str, explicit_targets: list[str]
) -> dict[str, Any]:
    all_prompt_ids = [str(row.get("prompt_id", "")).strip() for row in prompts]
    all_prompt_ids = [pid for pid in all_prompt_ids if pid]
    selected: list[str]
    if explicit_targets:
        explicit_set = {value for value in explicit_targets if value}
        selected = [pid for pid in all_prompt_ids if pid in explicit_set]
    elif profile == "fast":
        selected = _pick_broad_batch(prompts)
    else:
        selected = all_prompt_ids

    row_lookup = {str(row.get("prompt_id", "")).strip(): row for row in prompts}
    rows: list[dict[str, Any]] = []
    for prompt_id in selected:
        row = row_lookup.get(prompt_id, {})
        rows.append(
            {
                "prompt_id": prompt_id,
                "query_text": str(row.get("query_text", "")),
                "expected_row_markers": list(row.get("expected_row_markers") or []),
                "reasoning": str(row.get("reasoning", "")),
            }
        )

    return {
        "manifest_id": f"writer_targets_{_now_slug()}",
        "profile": profile,
        "target_count": len(rows),
        "targets": rows,
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")
