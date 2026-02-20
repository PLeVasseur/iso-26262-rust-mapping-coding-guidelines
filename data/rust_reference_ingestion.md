# Rust Reference SQLite Ingestion Notes

## Scope

- Database: `rust_reference.sqlite`
- Rank: 1 (sequential SQLite KB execution)
- Source: `https://github.com/rust-lang/reference.git`

## Build Command

```bash
uv run python scripts/sqlite_build_rust_reference.py
```

Optional pinned revision:

```bash
uv run python scripts/sqlite_build_rust_reference.py --reference-revision <commit-sha>
```

## Schema Migration Command

```bash
uv run python scripts/sqlite_migrate_rust_reference_schema.py
```

## Validation Command

```bash
uv run python scripts/sqlite_validate_rust_reference.py
```

## Smoke Command

```bash
uv run python scripts/sqlite_smoke_rust_reference.py
```

## Query Reasonableness Suite

Query definitions and expected results are stored separately:

- `data/query_testsets/rust_reference_table1_queries.yaml`
- `data/query_testsets/rust_reference_table1_expected.yaml`

Run the 45-case verification suite:

```bash
uv run python scripts/sqlite_verify_rust_reference_query_set.py
```

## Timestamping

- Snapshot timestamp is stored in `snapshots.fetched_at`.
- Document-level timestamp is stored in `source_documents.source_fetched_at`.
- Section and statement timestamps are stored in `sections.source_fetched_at` and `statements.source_fetched_at`.
- Table 1 row rationale timestamp is stored in `row_verdicts.rationale_timestamp`.
- Query-suite reports are written under `.cache/sqlite_kb/reports/rust_reference/`.
