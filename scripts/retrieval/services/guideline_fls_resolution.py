from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _load_metadata(metadata_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(str(metadata_json or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def get_raw_guideline_fls(guideline_id: str, *, db_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    try:
        row = connection.execute(
            (
                "SELECT source_file_path, raw_fls_id, raw_fls_present, source_revision, "
                "source_hash, last_ingested_at FROM guideline_fls_source_mappings "
                "WHERE guideline_id = ?"
            ),
            (guideline_id,),
        ).fetchone()
        if row is not None:
            return {
                "guideline_id": guideline_id,
                "source_file_path": str(row[0]),
                "raw_fls_id": str(row[1]),
                "raw_fls_present": bool(row[2]),
                "source_revision": str(row[3]),
                "source_hash": str(row[4]),
                "last_ingested_at": str(row[5]),
            }
        row = connection.execute(
            (
                "SELECT source_file_path, metadata_json, source_revision, source_hash, ingested_at "
                "FROM guideline_records WHERE guideline_id = ?"
            ),
            (guideline_id,),
        ).fetchone()
        if row is None:
            return {
                "guideline_id": guideline_id,
                "source_file_path": "",
                "raw_fls_id": "",
                "raw_fls_present": False,
                "source_revision": "",
                "source_hash": "",
                "last_ingested_at": "",
            }
        metadata = _load_metadata(str(row[1]))
        raw_fls_id = str(metadata.get("fls", "") or "").strip()
        return {
            "guideline_id": guideline_id,
            "source_file_path": str(row[0]),
            "raw_fls_id": raw_fls_id,
            "raw_fls_present": bool(raw_fls_id),
            "source_revision": str(row[2]),
            "source_hash": str(row[3]),
            "last_ingested_at": str(row[4]),
        }
    finally:
        connection.close()


def get_guideline_fls_resolution_state(guideline_id: str, *, db_path: Path) -> dict[str, Any]:
    raw = get_raw_guideline_fls(guideline_id, db_path=db_path)
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    try:
        row = connection.execute(
            (
                "SELECT effective_fls_id, resolution_kind, resolution_status, audit_run_id, "
                "evidence_source_id, rationale_text, approved_by, approved_at, updated_at "
                "FROM guideline_fls_resolution_overrides WHERE guideline_id = ?"
            ),
            (guideline_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return {
            **raw,
            "effective_fls_id": str(raw.get("raw_fls_id", "")),
            "resolution_kind": "keep_raw",
            "resolution_status": "raw_only",
            "audit_run_id": "",
            "evidence_source_id": "",
            "rationale_text": "",
            "approved_by": "",
            "approved_at": "",
            "updated_at": "",
            "mapping_state_source": "raw",
        }
    effective_fls_id = str(row[0] or "").strip()
    resolution_status = str(row[2] or "").strip()
    use_override = resolution_status == "approved"
    return {
        **raw,
        "effective_fls_id": effective_fls_id if use_override else str(raw.get("raw_fls_id", "")),
        "resolution_kind": str(row[1] or ""),
        "resolution_status": resolution_status,
        "audit_run_id": str(row[3] or ""),
        "evidence_source_id": str(row[4] or ""),
        "rationale_text": str(row[5] or ""),
        "approved_by": str(row[6] or ""),
        "approved_at": str(row[7] or ""),
        "updated_at": str(row[8] or ""),
        "mapping_state_source": "override" if use_override else "raw",
    }


def get_effective_guideline_fls(guideline_id: str, *, db_path: Path) -> str:
    state = get_guideline_fls_resolution_state(guideline_id, db_path=db_path)
    return str(state.get("effective_fls_id", "") or "").strip()
