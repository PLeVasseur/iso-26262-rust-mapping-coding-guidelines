BEGIN;

CREATE TABLE IF NOT EXISTS modules (
    module_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    title TEXT NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS items (
    item_id TEXT PRIMARY KEY,
    module_id TEXT NOT NULL,
    fq_path TEXT NOT NULL,
    item_kind TEXT NOT NULL,
    signature TEXT NOT NULL,
    stability TEXT NOT NULL,
    safety_summary TEXT NOT NULL,
    panic_summary TEXT NOT NULL,
    FOREIGN KEY(module_id) REFERENCES modules(module_id)
);

CREATE TABLE IF NOT EXISTS contracts (
    contract_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    contract_kind TEXT NOT NULL,
    text TEXT NOT NULL,
    source_anchor TEXT NOT NULL,
    FOREIGN KEY(item_id) REFERENCES items(item_id)
);

CREATE TABLE IF NOT EXISTS examples (
    example_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    example_kind TEXT NOT NULL,
    code TEXT NOT NULL,
    notes TEXT NOT NULL,
    source_anchor TEXT NOT NULL,
    FOREIGN KEY(item_id) REFERENCES items(item_id)
);

CREATE INDEX IF NOT EXISTS idx_items_fq_path ON items(fq_path);
CREATE INDEX IF NOT EXISTS idx_items_kind ON items(item_kind);
CREATE INDEX IF NOT EXISTS idx_contracts_item_kind ON contracts(item_id, contract_kind);

COMMIT;
