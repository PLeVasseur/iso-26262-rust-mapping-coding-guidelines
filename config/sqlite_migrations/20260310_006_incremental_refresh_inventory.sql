CREATE TABLE IF NOT EXISTS source_inventory_documents (
    corpus TEXT NOT NULL,
    document_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL DEFAULT '',
    content_sha256 TEXT NOT NULL DEFAULT '',
    metadata_sha256 TEXT NOT NULL DEFAULT '',
    last_materialized_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (corpus, document_id)
);

CREATE TABLE IF NOT EXISTS source_inventory_sections (
    corpus TEXT NOT NULL,
    section_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL DEFAULT '',
    metadata_sha256 TEXT NOT NULL DEFAULT '',
    last_materialized_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (corpus, section_id)
);

CREATE TABLE IF NOT EXISTS source_inventory_units (
    corpus TEXT NOT NULL,
    unit_kind TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    parent_id TEXT NOT NULL DEFAULT '',
    content_sha256 TEXT NOT NULL DEFAULT '',
    metadata_sha256 TEXT NOT NULL DEFAULT '',
    derived_from_sha256 TEXT NOT NULL DEFAULT '',
    retrieval_eligible INTEGER NOT NULL DEFAULT 0,
    last_materialized_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (corpus, unit_kind, unit_id)
);

CREATE TABLE IF NOT EXISTS guideline_inventory (
    guideline_id TEXT PRIMARY KEY,
    source_file_path TEXT NOT NULL DEFAULT '',
    source_hash TEXT NOT NULL DEFAULT '',
    metadata_hash TEXT NOT NULL DEFAULT '',
    blocks_hash TEXT NOT NULL DEFAULT '',
    citations_hash TEXT NOT NULL DEFAULT '',
    bibliography_hash TEXT NOT NULL DEFAULT '',
    last_ingested_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS materialization_deltas (
    run_id TEXT PRIMARY KEY,
    corpus TEXT NOT NULL,
    mode TEXT NOT NULL,
    base_snapshot_id TEXT NOT NULL DEFAULT '',
    target_snapshot_id TEXT NOT NULL DEFAULT '',
    delta_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embedding_reuse_audit (
    run_id TEXT NOT NULL,
    corpus TEXT NOT NULL,
    model_fingerprint TEXT NOT NULL DEFAULT '',
    reused_count INTEGER NOT NULL DEFAULT 0,
    recomputed_count INTEGER NOT NULL DEFAULT 0,
    audited_at TEXT NOT NULL,
    PRIMARY KEY (run_id, corpus, model_fingerprint)
);
