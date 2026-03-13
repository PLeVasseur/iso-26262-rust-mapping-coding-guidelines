BEGIN;

CREATE TABLE IF NOT EXISTS chunk_fts_rowids (
    chunk_uid TEXT PRIMARY KEY,
    fts_rowid INTEGER NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_chunk_fts_rowids_fts_rowid
ON chunk_fts_rowids(fts_rowid);

COMMIT;
