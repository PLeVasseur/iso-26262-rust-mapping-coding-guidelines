from __future__ import annotations

import sqlite3
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path

from retrieval.build.chunk_fts_validation import validate_chunk_fts_mapping_db
from retrieval.build.reports import (
    validate_chunk_first_db,
    validate_guidelines_repo_db,
    write_current_chunk_first_validation_report,
    write_current_guidelines_repo_validation_report,
)
from retrieval.core.provenance import (
    apply_pending_migrations,
    canonical_json_hash,
    read_latest_pipeline_run,
    record_pipeline_run,
)
from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.corpora.registry import get_corpus_adapter
from retrieval.services._invoke import run_main
from retrieval.services.capability import emit_unsupported
from retrieval.services.ws7_prework_closure import maybe_refresh_ws7_prework_closure_packet
from sqlite_migrate_schema import main as migrate_main


def run(args: Namespace, *, root: Path) -> int:
    defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))
    if not defaults.supports_migrate:
        return emit_unsupported(
            corpus=defaults.corpus,
            operation="migrate",
            reason="corpus configuration disables migrate",
        )
    latest_migration_id, _ = apply_pending_migrations(defaults.db_path, root=root)

    argv = ["sqlite_migrate.py", "--db-path", str(defaults.db_path)]
    argv.extend(list(args.extra_args or []))
    status = run_main(migrate_main, argv)
    if status != 0:
        return status

    if defaults.corpus == "guidelines_repo":
        connection = sqlite3.connect(defaults.db_path)
        try:
            row = connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()
            count = int(row[0]) if row else 0
            if count == 0:
                ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                connection.execute(
                    (
                        "INSERT INTO snapshots("
                        "snapshot_id, commit_sha, source_url, fetched_at, sha256"
                        ") VALUES(?, ?, ?, ?, ?)"
                    ),
                    (
                        f"bootstrap-{ts}",
                        "bootstrap",
                        "bootstrap://guidelines_repo",
                        datetime.now(UTC).isoformat(timespec="seconds"),
                        canonical_json_hash({"bootstrap": True, "corpus": defaults.corpus}),
                    ),
                )
                connection.commit()
        finally:
            connection.close()

    latest_migration_id, _ = apply_pending_migrations(defaults.db_path, root=root)
    mapping = validate_chunk_fts_mapping_db(defaults.db_path)
    if mapping.get("applicable") and not mapping.get("passed", False):
        raise RuntimeError(
            f"migrate service refuses success for stale chunk_fts_rowids mapping: {mapping}"
        )
    latest_run = read_latest_pipeline_run(defaults.db_path, corpus=defaults.corpus)
    source_state = get_corpus_adapter(defaults.corpus).compute_source_state(defaults.db_path)
    model_fingerprint = canonical_json_hash(
        {
            "embed_model_id": "Qwen/Qwen3-Embedding-4B",
            "reranker_model_id": "BAAI/bge-reranker-v2-m3",
            "embedding_dim": 0,
        }
    )
    run_suffix = "bootstrap" if latest_run is None else "refresh"
    run_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    record_pipeline_run(
        db_path=defaults.db_path,
        run_id=f"migrate::{defaults.corpus}::{run_suffix}::{run_stamp}",
        corpus=defaults.corpus,
        source_state=source_state,
        schema_migration_id=latest_migration_id,
        ingest_strategy=defaults.ingest_strategy,
        ingest_strategy_version="1",
        ingest_params={
            "target_min_tokens": defaults.chunk_target_min_tokens,
            "target_max_tokens": defaults.chunk_target_max_tokens,
            "overlap_percent": defaults.chunk_overlap_percent,
        },
        retrieval_profile_id=defaults.profile_name,
        eval_policy_id=defaults.eval_policy_path.stem,
        model_fingerprint=model_fingerprint,
        allow_provenance_mismatch=False,
    )
    if defaults.corpus == "guidelines_repo":
        current_report = validate_guidelines_repo_db(defaults.db_path)
        write_current_guidelines_repo_validation_report(
            report_root=defaults.report_root,
            payload=current_report,
        )
        maybe_refresh_ws7_prework_closure_packet(
            root=root,
            deferred_items=["WS7 staged runtime implementation"],
        )
    elif mapping.get("applicable"):
        current_report = validate_chunk_first_db(defaults.db_path, corpus=defaults.corpus)
        write_current_chunk_first_validation_report(
            report_root=defaults.report_root,
            corpus=defaults.corpus,
            payload=current_report,
        )
        maybe_refresh_ws7_prework_closure_packet(
            root=root,
            deferred_items=["WS7 staged runtime implementation"],
        )
    return status
