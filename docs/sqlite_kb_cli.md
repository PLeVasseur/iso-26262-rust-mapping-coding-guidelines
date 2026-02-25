# sqlite_kb Unified CLI

Use `scripts/sqlite_kb.py` as the canonical command surface for retrieval workflows.

## Command Pattern

```bash
uv run python scripts/sqlite_kb.py <subcommand> --corpus <corpus> -- [subcommand flags]
```

Supported subcommands:

- `query`
- `eval`
- `eval-report`
- `build`
- `materialize`
- `smoke`
- `capture`
- `verify`
- `validate`
- `migrate`

## Override Precedence

Defaults resolve in this order:

1. explicit CLI flags
2. selected retrieval profile values
3. corpus config pack defaults (`config/corpora/<corpus>.yaml`)
4. global fallback defaults

Environment variables are not used for runtime defaults.

## Examples

Evaluate rust reference with known-good defaults:

```bash
uv run python scripts/sqlite_kb.py eval --corpus rust_reference
```

Evaluate with explicit overrides:

```bash
uv run python scripts/sqlite_kb.py eval --corpus rust_reference -- \
  --retrieval-profile-path config/retrieval_profiles/rust_reference_least_bad.yaml \
  --top-k 10 --candidate-limit 5000 --semantic-retries 0
```

Run materialization for core docs:

```bash
uv run python scripts/sqlite_kb.py materialize --corpus core_docs
```

Generate human-readable eval report markdown from eval artifact:

```bash
uv run python scripts/sqlite_kb.py eval-report --corpus core_docs -- \
  --eval-path .cache/sqlite_kb/reports/core_docs/phase_a/20260224T230000Z/eval.json
```

## Canonical Verification Command

Use this command sequence as the canonical repo verification path for retrieval changes:

```bash
uv run ruff check scripts tests/unit/sqlite_kb && \
uv run python -m unittest discover -s tests/unit/sqlite_kb -p 'test_*.py' && \
uv run rg -n "Core docs coverage for ISO 26262 Table 1 row|core-docs::.*::section" scripts/retrieval/builders/core_docs_builder.py && exit 1 || true
```

The final grep guard must not match anything. If it matches, synthetic row-summary core_docs
build behavior has reappeared and the change is rejected.
