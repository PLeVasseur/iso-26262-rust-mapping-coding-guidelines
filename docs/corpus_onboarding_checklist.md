# Corpus Onboarding Checklist

Add a new corpus without adding corpus-specific orchestration scripts.

## Required Files

1. Corpus adapter registration
   - `scripts/retrieval/corpora/<corpus>_adapter.py`
   - `scripts/retrieval/corpora/registry.py`
2. Corpus config pack
   - `config/corpora/<corpus>.yaml`
3. Query contract
   - `config/sqlite_query_contracts/<corpus>.yaml`
4. Rewrite rules
   - `config/sqlite_query_rewrite/<corpus>_rewrite.yaml`
5. Retrieval profile(s)
   - `config/retrieval_profiles/<corpus>_control.yaml`
   - optional least-bad profile
6. Eval policy
   - `config/eval_policies/<corpus>.yaml`
7. Eval query set
   - `data/query_testsets/<corpus>_table1_retrieval_eval.yaml`

## Validation

1. `uv run python scripts/sqlite_kb.py query --corpus <corpus> -- --mode lexical --query-text "sanity"`
2. `uv run python scripts/sqlite_kb.py eval --corpus <corpus>`
3. `uv run ruff check scripts tests/unit/sqlite_kb`
4. `uv run python -m unittest tests/unit/sqlite_kb/test_retrieval_*.py`

## Non-Regression Requirement

- Existing rust-reference quality/runtime parity must remain unchanged after corpus onboarding.
