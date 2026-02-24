# sqlite_kb Unified CLI

Use `scripts/sqlite_kb.py` as the canonical command surface for retrieval workflows.

## Command Pattern

```bash
uv run python scripts/sqlite_kb.py <subcommand> --corpus <corpus> -- [subcommand flags]
```

Supported subcommands:

- `query`
- `eval`
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
