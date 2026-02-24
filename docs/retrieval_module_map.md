# Retrieval Module Map

This map is the canonical place to find retrieval command logic.

## Command Surface

- Canonical CLI: `scripts/sqlite_kb.py`
- Thin dispatchers:
  - `scripts/sqlite_query.py`
  - `scripts/sqlite_eval_retrieval.py`
  - `scripts/sqlite_build.py`
  - `scripts/sqlite_materialize_embeddings.py`
  - `scripts/sqlite_smoke.py`
  - `scripts/sqlite_capture_query_reviews.py`
  - `scripts/sqlite_verify_query_set.py`
  - `scripts/sqlite_validate.py`
  - `scripts/sqlite_migrate_schema.py`

## Operation Modules (command orchestration)

- `scripts/retrieval/operations/query.py`
- `scripts/retrieval/operations/eval.py`
- `scripts/retrieval/operations/build.py`
- `scripts/retrieval/operations/materialize.py`
- `scripts/retrieval/operations/smoke.py`
- `scripts/retrieval/operations/capture.py`
- `scripts/retrieval/operations/verify.py`
- `scripts/retrieval/operations/validate.py`
- `scripts/retrieval/operations/migrate.py`

## Reusable Services (shared internals)

- `scripts/retrieval/services/*.py`

## Builder Dispatch (corpus build internals)

- `scripts/retrieval/builders/*.py`

## Corpus-Specific Behavior

- adapters: `scripts/retrieval/corpora/*.py`
- ingest strategies: `scripts/retrieval/ingest/strategies/*.py`
- corpus configs: `config/corpora/*.yaml`
- retrieval profiles: `config/retrieval_profiles/*.yaml`
- eval policies: `config/eval_policies/*.yaml`
