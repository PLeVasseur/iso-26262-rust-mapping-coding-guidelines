CREATE TABLE IF NOT EXISTS schema_version (
    schema_id TEXT PRIMARY KEY,
    latest_migration_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS migration_history (
    migration_id TEXT PRIMARY KEY,
    checksum_sha256 TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    tool_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    corpus TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    source_timestamp TEXT NOT NULL,
    schema_migration_id TEXT NOT NULL,
    ingest_strategy TEXT NOT NULL,
    ingest_strategy_version TEXT NOT NULL,
    ingest_params_json TEXT NOT NULL,
    retrieval_profile_id TEXT NOT NULL,
    eval_policy_id TEXT NOT NULL,
    model_fingerprint TEXT NOT NULL,
    pipeline_fingerprint TEXT NOT NULL,
    allow_provenance_mismatch INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_corpus_created
ON pipeline_runs(corpus, created_at DESC);
