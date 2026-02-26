BEGIN;

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    commit_sha TEXT NOT NULL,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    sha256 TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_fetched_at
ON snapshots(fetched_at DESC);

CREATE TABLE IF NOT EXISTS guideline_records (
    guideline_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_file_path TEXT NOT NULL,
    quality_label TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    export_topic TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_guideline_records_source_file_path
ON guideline_records(source_file_path);

CREATE INDEX IF NOT EXISTS idx_guideline_records_quality_label
ON guideline_records(quality_label);

CREATE TABLE IF NOT EXISTS guideline_blocks (
    block_id TEXT PRIMARY KEY,
    guideline_id TEXT NOT NULL,
    block_type TEXT NOT NULL,
    order_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    FOREIGN KEY(guideline_id) REFERENCES guideline_records(guideline_id) ON DELETE CASCADE,
    UNIQUE(guideline_id, block_type, order_index)
);

CREATE INDEX IF NOT EXISTS idx_guideline_blocks_guideline
ON guideline_blocks(guideline_id, order_index);

CREATE TABLE IF NOT EXISTS guideline_bibliography (
    bib_key TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    source_file_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guideline_bib_links (
    guideline_id TEXT NOT NULL,
    bib_key TEXT NOT NULL,
    PRIMARY KEY(guideline_id, bib_key),
    FOREIGN KEY(guideline_id) REFERENCES guideline_records(guideline_id) ON DELETE CASCADE,
    FOREIGN KEY(bib_key) REFERENCES guideline_bibliography(bib_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_guideline_bib_links_bib_key
ON guideline_bib_links(bib_key);

CREATE TABLE IF NOT EXISTS guideline_citations (
    citation_id TEXT PRIMARY KEY,
    guideline_id TEXT NOT NULL,
    block_id TEXT NOT NULL,
    ref_target TEXT NOT NULL,
    order_index INTEGER NOT NULL,
    FOREIGN KEY(guideline_id) REFERENCES guideline_records(guideline_id) ON DELETE CASCADE,
    FOREIGN KEY(block_id) REFERENCES guideline_blocks(block_id) ON DELETE CASCADE,
    UNIQUE(block_id, order_index)
);

CREATE INDEX IF NOT EXISTS idx_guideline_citations_guideline
ON guideline_citations(guideline_id, block_id, order_index);

CREATE TABLE IF NOT EXISTS guideline_exemplars (
    guideline_id TEXT PRIMARY KEY,
    added_at TEXT NOT NULL,
    rationale TEXT NOT NULL,
    FOREIGN KEY(guideline_id) REFERENCES guideline_records(guideline_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS guideline_export_runs (
    run_id TEXT PRIMARY KEY,
    corpus TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    output_root TEXT NOT NULL,
    file_count INTEGER NOT NULL,
    output_digest TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_guideline_export_runs_created
ON guideline_export_runs(created_at DESC);

COMMIT;
