from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.core.provenance import (  # noqa: E402
    apply_pending_migrations,
    assert_schema_up_to_date,
    record_pipeline_run,
)
from retrieval.services.provenance_guard import enforce_provenance_guard  # noqa: E402


class ProvenanceGuardTests(unittest.TestCase):
    def test_schema_stale_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.sqlite"
            latest, _ = apply_pending_migrations(db_path, root=ROOT)
            self.assertTrue(latest)
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
                extra_args=["--allow-provenance-mismatch"],
            )


if __name__ == "__main__":
    unittest.main()
