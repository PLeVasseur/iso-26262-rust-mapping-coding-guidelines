from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import unittest
from argparse import Namespace
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.core.provenance import (  # noqa: E402
    apply_pending_migrations,
    assert_schema_up_to_date,
    record_pipeline_run,
)
from retrieval.build.chunk_fts_validation import validate_chunk_fts_mapping_db  # noqa: E402
from retrieval.build.chunk_fts_validation import validate_chunk_fts_mapping  # noqa: E402
from retrieval.operations.migrate import migrate_schema  # noqa: E402
from retrieval.services.migrate_service import run as run_migrate_service  # noqa: E402
from retrieval.services.provenance_guard import enforce_provenance_guard  # noqa: E402


class ProvenanceGuardTests(unittest.TestCase):
    def test_schema_stale_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.sqlite"
            latest, _ = apply_pending_migrations(db_path, root=ROOT)
            self.assertTrue(latest)
            self.assertEqual(latest, "20260309_005_chunk_fts_rowids")
            connection = sqlite3.connect(db_path)
            try:
                with connection:
                    connection.execute(
                        """
                        UPDATE schema_version
                        SET latest_migration_id = ?, schema_version = ?
                        WHERE schema_id = ?
                        """,
                        ("stale", 0, "sqlite_kb"),
                    )
                with self.assertRaises(RuntimeError):
                    assert_schema_up_to_date(db_path, root=ROOT)
            finally:
                connection.close()

    def test_provenance_guard_hard_fail_and_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.sqlite"
            latest, _ = apply_pending_migrations(db_path, root=ROOT)

            connection = sqlite3.connect(db_path)
            try:
                with connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS snapshots(
                            snapshot_id TEXT PRIMARY KEY,
                            commit_sha TEXT NOT NULL,
                            source_url TEXT NOT NULL,
                            fetched_at TEXT NOT NULL,
                            sha256 TEXT NOT NULL
                        )
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO snapshots(
                            snapshot_id,
                            commit_sha,
                            source_url,
                            fetched_at,
                            sha256
                        ) VALUES(?, ?, ?, ?, ?)
                        """,
                        (
                            "snap-1",
                            "abc123",
                            "https://example.invalid",
                            "2026-02-24T00:00:00+00:00",
                            "sha-snap",
                        ),
                    )
            finally:
                connection.close()

            record_pipeline_run(
                db_path=db_path,
                run_id="run-1",
                corpus="rust_reference",
                source_state={
                    "source_revision": "abc123",
                    "source_fingerprint": "sha-snap",
                    "source_timestamp": "2026-02-24T00:00:00+00:00",
                    "details": {"source_url": "https://example.invalid"},
                },
                schema_migration_id=latest,
                ingest_strategy="rust_md_v1",
                ingest_strategy_version="1",
                ingest_params={"target_min_tokens": 150, "target_max_tokens": 500},
                retrieval_profile_id="rust_reference_control",
                eval_policy_id="rust_reference",
                model_fingerprint="model-fingerprint",
                allow_provenance_mismatch=False,
            )

            with self.assertRaises(RuntimeError):
                enforce_provenance_guard(
                    root=ROOT,
                    operation="query",
                    corpus="rust_reference",
                    default_db_path=db_path,
                    default_profile_name="rust_reference_control",
                    default_eval_policy_id="rust_reference",
                    default_ingest_strategy="rust_md_v1",
                    chunk_target_min_tokens=120,
                    chunk_target_max_tokens=500,
                    chunk_overlap_percent=0.0,
                    extra_args=[],
                )

            enforce_provenance_guard(
                root=ROOT,
                operation="query",
                corpus="rust_reference",
                default_db_path=db_path,
                default_profile_name="rust_reference_control",
                default_eval_policy_id="rust_reference",
                default_ingest_strategy="rust_md_v1",
                chunk_target_min_tokens=120,
                chunk_target_max_tokens=500,
                chunk_overlap_percent=0.0,
                extra_args=["--allow-provenance-mismatch"],
            )

    def test_apply_pending_migrations_backfills_chunk_mapping_for_existing_chunk_db(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy_chunk.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "CREATE TABLE chunks(chunk_uid TEXT PRIMARY KEY, clean_text TEXT)"
                )
                connection.execute(
                    "CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_uid UNINDEXED, chunk_text)"
                )
                connection.execute(
                    "INSERT INTO chunks(chunk_uid, clean_text) VALUES(?, ?)",
                    ("chunk-1", "raw pointer dereference safety"),
                )
                connection.execute(
                    "INSERT INTO chunks_fts(chunk_uid, chunk_text) VALUES(?, ?)",
                    ("chunk-1", "raw pointer dereference safety"),
                )
                connection.commit()
            finally:
                connection.close()

            latest, _ = apply_pending_migrations(db_path, root=ROOT)
            self.assertEqual(latest, "20260309_005_chunk_fts_rowids")
            mapping = validate_chunk_fts_mapping_db(db_path)
            self.assertTrue(mapping.get("passed"), mapping)
            self.assertEqual(mapping.get("chunk_fts_rowids_count"), 1)

    def test_chunk_fts_migration_sql_alone_does_not_hide_backfill_truth(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy_chunk.sqlite"
            migration_sql = (
                ROOT / "config" / "sqlite_migrations" / "20260309_005_chunk_fts_rowids.sql"
            ).read_text(encoding="utf-8")

            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "CREATE TABLE chunks(chunk_uid TEXT PRIMARY KEY, clean_text TEXT)"
                )
                connection.execute(
                    "CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_uid UNINDEXED, chunk_text)"
                )
                connection.execute(
                    "INSERT INTO chunks(chunk_uid, clean_text) VALUES(?, ?)",
                    ("chunk-1", "raw pointer dereference safety"),
                )
                connection.execute(
                    "INSERT INTO chunks_fts(chunk_uid, chunk_text) VALUES(?, ?)",
                    ("chunk-1", "raw pointer dereference safety"),
                )
                connection.executescript(migration_sql)
                connection.commit()

                rows = connection.execute(
                    "SELECT chunk_uid, fts_rowid FROM chunk_fts_rowids ORDER BY chunk_uid ASC"
                ).fetchall()
                self.assertEqual(rows, [])
            finally:
                connection.close()

    def test_apply_pending_migrations_does_not_silently_repair_stale_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stale_chunk.sqlite"
            migration_sql = (
                ROOT / "config" / "sqlite_migrations" / "20260309_005_chunk_fts_rowids.sql"
            ).read_text(encoding="utf-8")
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "CREATE TABLE chunks(chunk_uid TEXT PRIMARY KEY, clean_text TEXT)"
                )
                connection.execute(
                    "CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_uid UNINDEXED, chunk_text)"
                )
                connection.execute(
                    "INSERT INTO chunks(chunk_uid, clean_text) VALUES(?, ?)",
                    ("chunk-1", "raw pointer dereference safety"),
                )
                connection.execute(
                    "INSERT INTO chunks_fts(chunk_uid, chunk_text) VALUES(?, ?)",
                    ("chunk-1", "raw pointer dereference safety"),
                )
                connection.execute(
                    "CREATE TABLE chunk_fts_rowids(chunk_uid TEXT PRIMARY KEY, fts_rowid INTEGER NOT NULL UNIQUE)"
                )
                checksum = hashlib.sha256(migration_sql.encode("utf-8")).hexdigest()
                connection.execute(
                    "CREATE TABLE migration_history(migration_id TEXT PRIMARY KEY, checksum_sha256 TEXT NOT NULL, applied_at TEXT NOT NULL, tool_version TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO migration_history(migration_id, checksum_sha256, applied_at, tool_version) VALUES(?, ?, ?, ?)",
                    (
                        "20260309_005_chunk_fts_rowids",
                        checksum,
                        "2026-03-09T00:00:00+00:00",
                        "test",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            apply_pending_migrations(db_path, root=ROOT)
            connection = sqlite3.connect(db_path)
            try:
                mapping = validate_chunk_fts_mapping(connection)
            finally:
                connection.close()
            self.assertFalse(mapping.get("passed"), mapping)

    def test_migrate_service_refuses_pipeline_run_when_chunk_mapping_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "rust_reference.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "CREATE TABLE chunks(chunk_uid TEXT PRIMARY KEY, clean_text TEXT)"
                )
                connection.execute(
                    "CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_uid UNINDEXED, chunk_text)"
                )
                connection.execute(
                    "CREATE TABLE chunk_fts_rowids(chunk_uid TEXT PRIMARY KEY, fts_rowid INTEGER NOT NULL UNIQUE)"
                )
                connection.execute(
                    "CREATE INDEX idx_chunk_fts_rowids_fts_rowid ON chunk_fts_rowids(fts_rowid)"
                )
                connection.execute(
                    "CREATE TABLE migration_history(migration_id TEXT PRIMARY KEY, checksum_sha256 TEXT NOT NULL, applied_at TEXT NOT NULL, tool_version TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE schema_version(schema_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, latest_migration_id TEXT NOT NULL, updated_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO chunks(chunk_uid, clean_text) VALUES(?, ?)",
                    ("chunk-1", "raw pointer dereference safety"),
                )
                connection.execute(
                    "INSERT INTO chunks_fts(chunk_uid, chunk_text) VALUES(?, ?)",
                    ("chunk-1", "raw pointer dereference safety"),
                )
                connection.execute(
                    "INSERT INTO schema_version(schema_id, schema_version, latest_migration_id, updated_at) VALUES(?, ?, ?, ?)",
                    ("sqlite_kb", 5, "20260309_005_chunk_fts_rowids", "2026-03-09T00:00:00+00:00"),
                )
                connection.commit()
            finally:
                connection.close()

            defaults = SimpleNamespace(
                corpus="rust_reference",
                supports_migrate=True,
                db_path=db_path,
                report_root=temp_root / "reports",
                profile_name="rust_reference_control",
                eval_policy_path=Path("config/eval/rust_reference.yaml"),
                ingest_strategy="rust_md_v1",
                chunk_target_min_tokens=150,
                chunk_target_max_tokens=500,
                chunk_overlap_percent=0.0,
            )
            args = Namespace(corpus="rust_reference", extra_args=[])

            with patch(
                "retrieval.services.migrate_service.load_corpus_runtime_defaults",
                return_value=defaults,
            ):
                with patch(
                    "retrieval.services.migrate_service.apply_pending_migrations",
                    return_value=("20260309_005_chunk_fts_rowids", 0),
                ):
                    with patch("retrieval.services.migrate_service.run_main", return_value=0):
                        with patch(
                            "retrieval.services.migrate_service.record_pipeline_run"
                        ) as record_run:
                            with self.assertRaisesRegex(
                                RuntimeError, "stale chunk_fts_rowids mapping"
                            ):
                                run_migrate_service(args, root=ROOT)
                            record_run.assert_not_called()

    def test_migrate_schema_fails_closed_when_chunk_mapping_is_corrupted_after_refresh(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "CREATE TABLE chunks(chunk_uid TEXT PRIMARY KEY, section_id TEXT, clean_text TEXT)"
                )
                connection.execute(
                    "CREATE TABLE sections(section_id TEXT PRIMARY KEY, heading TEXT)"
                )
                connection.execute(
                    "CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_uid UNINDEXED, section_id UNINDEXED, section_heading, chunk_text)"
                )
                connection.execute(
                    "INSERT INTO sections(section_id, heading) VALUES(?, ?)",
                    ("sec-1", "Section 1"),
                )
                connection.execute(
                    "INSERT INTO chunks(chunk_uid, section_id, clean_text) VALUES(?, ?, ?)",
                    ("chunk-1", "sec-1", "raw pointer dereference safety"),
                )
                connection.commit()
            finally:
                connection.close()

            from retrieval.operations.migrate import (
                refresh_chunk_fts_rowids as original_refresh_chunk_fts_rowids,
            )

            def _refresh_then_corrupt(connection: sqlite3.Connection):
                result = original_refresh_chunk_fts_rowids(connection)
                row = connection.execute(
                    "SELECT chunk_uid FROM chunk_fts_rowids ORDER BY chunk_uid ASC LIMIT 1"
                ).fetchone()
                if row is not None:
                    connection.execute(
                        "DELETE FROM chunk_fts_rowids WHERE chunk_uid = ?",
                        (str(row[0]),),
                    )
                return result

            with patch(
                "retrieval.operations.migrate.refresh_chunk_fts_rowids",
                side_effect=_refresh_then_corrupt,
            ):
                with self.assertRaisesRegex(RuntimeError, "chunk_fts_rowids validation failed"):
                    migrate_schema(db_path)


if __name__ == "__main__":
    unittest.main()
