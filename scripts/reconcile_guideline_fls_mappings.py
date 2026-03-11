#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_FLS_DB = ROOT / ".cache" / "sqlite_kb" / "current" / "fls_spec.db"
DEFAULT_GUIDELINES_DB = ROOT / ".cache" / "sqlite_kb" / "current" / "guidelines_repo.sqlite"
DEFAULT_REPORT_PATH = (
    ROOT
    / ".cache"
    / "sqlite_kb"
    / "reports"
    / "fls_spec"
    / "ws7_guideline_fls_resolution_report.json"
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _history_id(guideline_id: str, recorded_at: str) -> str:
    import hashlib

    return hashlib.sha256(f"{guideline_id}::{recorded_at}".encode()).hexdigest()[:20]


def _ensure_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS guideline_fls_source_mappings (
            guideline_id TEXT PRIMARY KEY,
            source_file_path TEXT NOT NULL,
            raw_fls_id TEXT NOT NULL DEFAULT '',
            raw_fls_present INTEGER NOT NULL DEFAULT 0,
            source_revision TEXT NOT NULL DEFAULT '',
            source_hash TEXT NOT NULL DEFAULT '',
            last_ingested_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS guideline_fls_resolution_overrides (
            guideline_id TEXT PRIMARY KEY,
            effective_fls_id TEXT NOT NULL DEFAULT '',
            resolution_kind TEXT NOT NULL DEFAULT 'keep_raw',
            resolution_status TEXT NOT NULL DEFAULT 'proposed',
            audit_run_id TEXT NOT NULL DEFAULT '',
            evidence_source_id TEXT NOT NULL DEFAULT '',
            rationale_text TEXT NOT NULL DEFAULT '',
            approved_by TEXT NOT NULL DEFAULT '',
            approved_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS guideline_fls_resolution_candidates (
            audit_run_id TEXT NOT NULL,
            guideline_id TEXT NOT NULL,
            rank INTEGER NOT NULL,
            paragraph_id TEXT NOT NULL,
            document_link TEXT NOT NULL DEFAULT '',
            section_link TEXT NOT NULL DEFAULT '',
            candidate_source TEXT NOT NULL DEFAULT '',
            evidence_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (audit_run_id, guideline_id, rank, paragraph_id)
        );
        CREATE TABLE IF NOT EXISTS guideline_fls_resolution_history (
            history_id TEXT PRIMARY KEY,
            guideline_id TEXT NOT NULL,
            effective_fls_id TEXT NOT NULL DEFAULT '',
            resolution_kind TEXT NOT NULL DEFAULT '',
            resolution_status TEXT NOT NULL DEFAULT '',
            audit_run_id TEXT NOT NULL DEFAULT '',
            evidence_source_id TEXT NOT NULL DEFAULT '',
            rationale_text TEXT NOT NULL DEFAULT '',
            approved_by TEXT NOT NULL DEFAULT '',
            approved_at TEXT NOT NULL DEFAULT '',
            recorded_at TEXT NOT NULL DEFAULT ''
        );
        """
    )


def sync_from_ws7_audit(*, fls_db_path: Path, guidelines_db_path: Path) -> dict[str, object]:
    fls = sqlite3.connect(fls_db_path)
    guidelines = sqlite3.connect(guidelines_db_path)
    try:
        _ensure_tables(guidelines)
        run_row = fls.execute(
            "SELECT r.run_id FROM ws7_mapping_audit_runs AS r "
            "JOIN ws7_mapping_audit_rows AS a ON a.run_id = r.run_id "
            "WHERE a.classification IN ('stale_mapping','weak_mapping') "
            "GROUP BY r.run_id ORDER BY MAX(r.generated_at) DESC LIMIT 1"
        ).fetchone()
        if run_row is None:
            raise RuntimeError("missing_ws7_mapping_audit_run")
        audit_run_id = str(run_row[0])
        rows = fls.execute(
            (
                "SELECT source_id, rst_path, source_fls_id, classification, "
                "nearest_candidate_paragraphs_json, evidence_json "
                "FROM ws7_mapping_audit_rows WHERE run_id = ? "
                "AND classification IN ('stale_mapping','weak_mapping') ORDER BY source_id"
            ),
            (audit_run_id,),
        ).fetchall()
        synced = 0
        for source_id, rst_path, raw_fls_id, classification, candidates_json, evidence_json in rows:
            guideline_id = Path(str(rst_path)).stem
            candidates = json.loads(str(candidates_json or "[]"))
            evidence = json.loads(str(evidence_json or "{}"))
            recommended_kind = (
                "remap" if str(classification) == "weak_mapping" else "unresolved_expected"
            )
            effective_fls_id = str(raw_fls_id or "").strip()
            if recommended_kind == "unresolved_expected":
                effective_fls_id = "fls_UNRESOLVED"
            guidelines.execute(
                (
                    "INSERT OR REPLACE INTO guideline_fls_resolution_overrides("
                    "guideline_id, effective_fls_id, resolution_kind, resolution_status, "
                    "audit_run_id, evidence_source_id, rationale_text, approved_by, "
                    "approved_at, updated_at"
                    ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    guideline_id,
                    effective_fls_id,
                    recommended_kind,
                    "proposed",
                    audit_run_id,
                    str(source_id),
                    str(evidence.get("rationale", "")),
                    "",
                    "",
                    _now(),
                ),
            )
            guidelines.execute(
                (
                    "DELETE FROM guideline_fls_resolution_candidates "
                    "WHERE audit_run_id = ? AND guideline_id = ?"
                ),
                (audit_run_id, guideline_id),
            )
            for rank, candidate in enumerate(candidates, start=1):
                guidelines.execute(
                    (
                        "INSERT OR REPLACE INTO guideline_fls_resolution_candidates "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)"
                    ),
                    (
                        audit_run_id,
                        guideline_id,
                        rank,
                        str(candidate.get("paragraph_id", "")),
                        str(candidate.get("document_link", "")),
                        str(candidate.get("section_link", "")),
                        "nearest_fls",
                        json.dumps(candidate, sort_keys=True),
                    ),
                )
            synced += 1
        guidelines.commit()
        return {"audit_run_id": audit_run_id, "synced_guidelines": synced}
    finally:
        fls.close()
        guidelines.close()


def _record_history(connection: sqlite3.Connection, *, guideline_id: str) -> None:
    row = connection.execute(
        (
            "SELECT guideline_id, effective_fls_id, resolution_kind, resolution_status, "
            "audit_run_id, "
            "evidence_source_id, rationale_text, approved_by, approved_at, updated_at "
            "FROM guideline_fls_resolution_overrides WHERE guideline_id = ?"
        ),
        (guideline_id,),
    ).fetchone()
    if row is None:
        return
    recorded_at = _now()
    connection.execute(
        (
            "INSERT OR REPLACE INTO guideline_fls_resolution_history "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        ),
        (
            _history_id(guideline_id, recorded_at),
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            str(row[5]),
            str(row[6]),
            str(row[7]),
            str(row[8]),
            recorded_at,
        ),
    )


def update_override(
    *,
    guidelines_db_path: Path,
    guideline_id: str,
    effective_fls_id: str,
    resolution_kind: str,
    resolution_status: str,
    rationale_text: str,
    approved_by: str,
) -> dict[str, object]:
    connection = sqlite3.connect(guidelines_db_path)
    try:
        _ensure_tables(connection)
        prior = connection.execute(
            (
                "SELECT audit_run_id, evidence_source_id "
                "FROM guideline_fls_resolution_overrides WHERE guideline_id = ?"
            ),
            (guideline_id,),
        ).fetchone()
        audit_run_id = str((prior or ("", ""))[0])
        evidence_source_id = str((prior or ("", ""))[1])
        connection.execute(
            (
                "INSERT OR REPLACE INTO guideline_fls_resolution_overrides("
                "guideline_id, effective_fls_id, resolution_kind, resolution_status, audit_run_id, "
                "evidence_source_id, rationale_text, approved_by, approved_at, updated_at"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            (
                guideline_id,
                effective_fls_id,
                resolution_kind,
                resolution_status,
                audit_run_id,
                evidence_source_id,
                rationale_text,
                approved_by,
                _now() if resolution_status == "approved" else "",
                _now(),
            ),
        )
        _record_history(connection, guideline_id=guideline_id)
        connection.commit()
        return {"guideline_id": guideline_id, "status": resolution_status, "kind": resolution_kind}
    finally:
        connection.close()


def write_report(*, guidelines_db_path: Path, output_path: Path) -> dict[str, object]:
    connection = sqlite3.connect(guidelines_db_path)
    try:
        rows = connection.execute(
            "SELECT s.guideline_id, s.source_file_path, s.raw_fls_id, o.effective_fls_id, "
            "o.resolution_kind, o.resolution_status, o.rationale_text "
            "FROM guideline_fls_source_mappings AS s "
            "LEFT JOIN guideline_fls_resolution_overrides AS o "
            "ON o.guideline_id = s.guideline_id ORDER BY s.guideline_id"
        ).fetchall()
    finally:
        connection.close()
    payload = {
        "generated_at": _now(),
        "row_count": len(rows),
        "rows": [
            {
                "guideline_id": str(row[0]),
                "source_file_path": str(row[1]),
                "raw_fls_id": str(row[2]),
                "effective_fls_id": str(row[3] or row[2] or ""),
                "resolution_kind": str(row[4] or "keep_raw"),
                "resolution_status": str(row[5] or "raw_only"),
                "rationale_text": str(row[6] or ""),
            }
            for row in rows
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile stale guideline FLS mappings in local SQLite overlays"
    )
    parser.add_argument("--fls-db", default=str(DEFAULT_FLS_DB))
    parser.add_argument("--guidelines-db", default=str(DEFAULT_GUIDELINES_DB))
    parser.add_argument("--report-path", default=str(DEFAULT_REPORT_PATH))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sync-from-ws7-audit", action="store_true")
    group.add_argument("--approve-remap", metavar="GUIDELINE_ID")
    group.add_argument("--approve-unresolved", metavar="GUIDELINE_ID")
    group.add_argument("--approve-corpus-gap", metavar="GUIDELINE_ID")
    group.add_argument("--reject", metavar="GUIDELINE_ID")
    group.add_argument("--report", action="store_true")
    parser.add_argument("--effective-fls-id", default="")
    parser.add_argument("--rationale", default="")
    parser.add_argument("--approved-by", default="local-operator")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fls_db_path = Path(args.fls_db).expanduser().resolve()
    guidelines_db_path = Path(args.guidelines_db).expanduser().resolve()
    if args.sync_from_ws7_audit:
        payload = sync_from_ws7_audit(
            fls_db_path=fls_db_path, guidelines_db_path=guidelines_db_path
        )
    elif args.approve_remap:
        effective = str(args.effective_fls_id).strip()
        if not effective:
            raise RuntimeError("missing_required_flag::--effective-fls-id")
        payload = update_override(
            guidelines_db_path=guidelines_db_path,
            guideline_id=str(args.approve_remap).strip(),
            effective_fls_id=effective,
            resolution_kind="remap",
            resolution_status="approved",
            rationale_text=str(args.rationale).strip(),
            approved_by=str(args.approved_by).strip(),
        )
    elif args.approve_unresolved:
        payload = update_override(
            guidelines_db_path=guidelines_db_path,
            guideline_id=str(args.approve_unresolved).strip(),
            effective_fls_id="fls_UNRESOLVED",
            resolution_kind="unresolved_expected",
            resolution_status="approved",
            rationale_text=str(args.rationale).strip(),
            approved_by=str(args.approved_by).strip(),
        )
    elif args.approve_corpus_gap:
        payload = update_override(
            guidelines_db_path=guidelines_db_path,
            guideline_id=str(args.approve_corpus_gap).strip(),
            effective_fls_id="fls_UNRESOLVED",
            resolution_kind="corpus_gap",
            resolution_status="approved",
            rationale_text=str(args.rationale).strip(),
            approved_by=str(args.approved_by).strip(),
        )
    elif args.reject:
        payload = update_override(
            guidelines_db_path=guidelines_db_path,
            guideline_id=str(args.reject).strip(),
            effective_fls_id="",
            resolution_kind="keep_raw",
            resolution_status="rejected",
            rationale_text=str(args.rationale).strip(),
            approved_by=str(args.approved_by).strip(),
        )
    else:
        payload = write_report(
            guidelines_db_path=guidelines_db_path,
            output_path=Path(args.report_path).expanduser().resolve(),
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
