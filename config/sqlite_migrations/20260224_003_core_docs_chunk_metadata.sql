BEGIN;

CREATE TABLE IF NOT EXISTS core_docs_chunk_metadata (
    chunk_uid TEXT PRIMARY KEY,
    item_path TEXT NOT NULL,
    item_kind TEXT NOT NULL,
    signature TEXT NOT NULL,
    stability TEXT NOT NULL,
    safety_notes TEXT NOT NULL,
    panic_behavior TEXT NOT NULL,
    example_snippets TEXT NOT NULL,
    target_triple TEXT NOT NULL,
    target_os TEXT NOT NULL,
    target_arch TEXT NOT NULL,
    target_env TEXT NOT NULL,
    cfg_signature TEXT NOT NULL,
    cfg_signature_sha256 TEXT NOT NULL,
    FOREIGN KEY(chunk_uid) REFERENCES chunks(chunk_uid)
);

CREATE INDEX IF NOT EXISTS idx_core_docs_chunk_metadata_item_kind
ON core_docs_chunk_metadata(item_kind);

CREATE INDEX IF NOT EXISTS idx_core_docs_chunk_metadata_target_triple
ON core_docs_chunk_metadata(target_triple);

CREATE INDEX IF NOT EXISTS idx_core_docs_chunk_metadata_cfg_sha
ON core_docs_chunk_metadata(cfg_signature_sha256);

COMMIT;
