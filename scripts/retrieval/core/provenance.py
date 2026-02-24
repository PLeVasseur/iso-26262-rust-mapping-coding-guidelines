from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

SCHEMA_ID = "sqlite_kb"
TOOL_VERSION = "sqlite-kb-clean-slate-v1"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def canonical_json_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _read_migration_manifest(root: Path) -> list[dict[str, str]]:
    manifest_path = root / "config/sqlite_migrations/manifest.yaml"
    with manifest_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    migrations = payload.get("migrations")
    if not isinstance(migrations, list) or not migrations:
        raise RuntimeError("Migration manifest must define non-empty migrations")

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in migrations:
        if not isinstance(entry, dict):
            raise RuntimeError("Migration entries must be mappings")
        migration_id = str(entry.get("id", "")).strip()
        migration_file = str(entry.get("file", "")).strip()
        if not migration_id or not migration_file:
            raise RuntimeError("Migration entry missing id/file")
        if migration_id in seen:
            raise RuntimeError(f"Duplicate migration id: {migration_id}")
        seen.add(migration_id)
        normalized.append({"id": migration_id, "file": migration_file})
    return normalized


def _applied_migrations(connection: sqlite3.Connection) -> dict[str, str]:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS migration_history (
            migration_id TEXT PRIMARY KEY,
            checksum_sha256 TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            tool_version TEXT NOT NULL
        )
        """
    )
    rows = connection.execute(
        "SELECT migration_id, checksum_sha256 FROM migration_history"
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def apply_pending_migrations(db_path: Path, *, root: Path) -> tuple[str, int]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        manifest = _read_migration_manifest(root)
        applied = _applied_migrations(connection)
        applied_count = 0
        latest_migration_id = ""

        for migration in manifest:
            migration_id = migration["id"]
            migration_path = (root / migration["file"]).resolve()
            sql_text = migration_path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql_text.encode("utf-8")).hexdigest()
            prior = applied.get(migration_id)
            if prior is not None:
                if prior != checksum:
                    raise RuntimeError(
                        "Migration checksum mismatch for "
                        f"{migration_id}: db={prior}, repo={checksum}"
                    )
                latest_migration_id = migration_id
                continue

            with connection:
                connection.executescript(sql_text)
                connection.execute(
                    """
                    INSERT INTO migration_history(
                        migration_id,
                        checksum_sha256,
                        applied_at,
                        tool_version
                    )
                    VALUES(?, ?, ?, ?)
                    """,
                    (migration_id, checksum, utc_now(), TOOL_VERSION),
                )
            applied_count += 1
            latest_migration_id = migration_id

        if manifest and not latest_migration_id:
            latest_migration_id = manifest[-1]["id"]

        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    schema_id TEXT PRIMARY KEY,
                    latest_migration_id TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO schema_version(
                    schema_id,
                    latest_migration_id,
                    schema_version,
                    updated_at
                )
                VALUES(?, ?, ?, ?)
                ON CONFLICT(schema_id) DO UPDATE SET
                    latest_migration_id = excluded.latest_migration_id,
                    schema_version = excluded.schema_version,
                    updated_at = excluded.updated_at
                """,
                (SCHEMA_ID, latest_migration_id, len(manifest), utc_now()),
            )
        return latest_migration_id, applied_count
    finally:
        connection.close()


def assert_schema_up_to_date(db_path: Path, *, root: Path) -> str:
    if not db_path.exists():
        raise RuntimeError(f"Database not found: {db_path}")
    manifest = _read_migration_manifest(root)
    expected_latest = manifest[-1]["id"]
    expected_version = len(manifest)

    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT latest_migration_id, schema_version FROM schema_version WHERE schema_id = ?",
            (SCHEMA_ID,),
        ).fetchone()
        if row is None:
            raise RuntimeError(
                "Database missing schema_version metadata. Run 'sqlite_kb.py migrate --corpus ...'"
            )
        latest = str(row[0])
        version = int(row[1])
        if latest != expected_latest or version != expected_version:
            raise RuntimeError(
                "Database migration state is stale. "
                f"expected ({expected_latest}, v{expected_version}) "
                f"but found ({latest}, v{version}). "
                "Run 'sqlite_kb.py migrate --corpus ...'"
            )
        return latest
    finally:
        connection.close()


def compute_source_state_from_db(db_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    try:
        row = connection.execute(
            """
            SELECT commit_sha, sha256, fetched_at, source_url
            FROM snapshots
            ORDER BY fetched_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            raise RuntimeError("No snapshot metadata found for source state")
        return {
            "source_revision": str(row[0]),
            "source_fingerprint": str(row[1]),
            "source_timestamp": str(row[2]),
            "details": {"source_url": str(row[3])},
        }
    finally:
        connection.close()


def build_pipeline_fingerprint_payload(
    *,
    corpus: str,
    source_state: dict[str, Any],
    schema_migration_id: str,
    ingest_strategy: str,
    ingest_strategy_version: str,
    ingest_params: dict[str, Any],
    retrieval_profile_id: str,
    eval_policy_id: str,
    model_fingerprint: str,
) -> dict[str, Any]:
    return {
        "corpus": str(corpus),
        "source_state": source_state,
        "schema_migration_id": str(schema_migration_id),
        "ingest_strategy": str(ingest_strategy),
        "ingest_strategy_version": str(ingest_strategy_version),
        "ingest_params": ingest_params,
        "retrieval_profile_id": str(retrieval_profile_id),
        "eval_policy_id": str(eval_policy_id),
        "model_fingerprint": str(model_fingerprint),
    }


def record_pipeline_run(
    *,
    db_path: Path,
    run_id: str,
    corpus: str,
    source_state: dict[str, Any],
    schema_migration_id: str,
    ingest_strategy: str,
    ingest_strategy_version: str,
    ingest_params: dict[str, Any],
    retrieval_profile_id: str,
    eval_policy_id: str,
    model_fingerprint: str,
    allow_provenance_mismatch: bool,
) -> str:
    payload = build_pipeline_fingerprint_payload(
        corpus=corpus,
        source_state=source_state,
        schema_migration_id=schema_migration_id,
        ingest_strategy=ingest_strategy,
        ingest_strategy_version=ingest_strategy_version,
        ingest_params=ingest_params,
        retrieval_profile_id=retrieval_profile_id,
        eval_policy_id=eval_policy_id,
        model_fingerprint=model_fingerprint,
    )
    fingerprint = canonical_json_hash(payload)

    connection = sqlite3.connect(db_path)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO pipeline_runs(
                    run_id,
                    corpus,
                    source_revision,
                    source_fingerprint,
                    source_timestamp,
                    schema_migration_id,
                    ingest_strategy,
                    ingest_strategy_version,
                    ingest_params_json,
                    retrieval_profile_id,
                    eval_policy_id,
                    model_fingerprint,
                    pipeline_fingerprint,
                    allow_provenance_mismatch,
                    created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    str(corpus),
                    str(source_state["source_revision"]),
                    str(source_state["source_fingerprint"]),
                    str(source_state["source_timestamp"]),
                    str(schema_migration_id),
                    str(ingest_strategy),
                    str(ingest_strategy_version),
                    json.dumps(ingest_params, sort_keys=True),
                    str(retrieval_profile_id),
                    str(eval_policy_id),
                    str(model_fingerprint),
                    str(fingerprint),
                    1 if allow_provenance_mismatch else 0,
                    utc_now(),
                ),
            )
    finally:
        connection.close()
    return fingerprint


def read_latest_pipeline_run(db_path: Path, *, corpus: str) -> dict[str, Any] | None:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    try:
        row = connection.execute(
            """
            SELECT
                run_id,
                pipeline_fingerprint,
                ingest_strategy,
                ingest_strategy_version,
                ingest_params_json,
                retrieval_profile_id,
                eval_policy_id,
                model_fingerprint,
                created_at
            FROM pipeline_runs
            WHERE corpus = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(corpus),),
        ).fetchone()
        if row is None:
            return None
        return {
            "run_id": str(row[0]),
            "pipeline_fingerprint": str(row[1]),
            "ingest_strategy": str(row[2]),
            "ingest_strategy_version": str(row[3]),
            "ingest_params": json.loads(str(row[4]) or "{}"),
            "retrieval_profile_id": str(row[5]),
            "eval_policy_id": str(row[6]),
            "model_fingerprint": str(row[7]),
            "created_at": str(row[8]),
        }
    finally:
        connection.close()
