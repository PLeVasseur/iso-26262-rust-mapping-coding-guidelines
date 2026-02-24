from __future__ import annotations

from argparse import Namespace
from pathlib import Path

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

    latest_migration_id, _ = apply_pending_migrations(defaults.db_path, root=root)
    latest_run = read_latest_pipeline_run(defaults.db_path, corpus=defaults.corpus)
    if latest_run is None:
        source_state = get_corpus_adapter(defaults.corpus).compute_source_state(defaults.db_path)
        model_fingerprint = canonical_json_hash(
            {
                "embed_model_id": "Qwen/Qwen3-Embedding-4B",
                "reranker_model_id": "BAAI/bge-reranker-v2-m3",
                "embedding_dim": 0,
            }
        )
        record_pipeline_run(
            db_path=defaults.db_path,
            run_id=f"migrate::{defaults.corpus}",
            corpus=defaults.corpus,
            source_state=source_state,
            schema_migration_id=latest_migration_id,
            ingest_strategy=defaults.ingest_strategy,
            ingest_strategy_version="1",
            ingest_params={
                "target_min_tokens": defaults.chunk_target_min_tokens,
                "target_max_tokens": defaults.chunk_target_max_tokens,
            },
            retrieval_profile_id=defaults.profile_name,
            eval_policy_id=defaults.eval_policy_path.stem,
            model_fingerprint=model_fingerprint,
            allow_provenance_mismatch=False,
        )
    return status
