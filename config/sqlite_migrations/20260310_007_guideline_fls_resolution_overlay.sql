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
