# ISO 26262 Rust Mapping Operationalization

This repo hosts the operational workflow for growing Rust coding guideline coverage from ISO 26262 seeds.

## Workflow

1. Resolve extractor paths and verify extractor health.
2. Run deterministic seed queries into `.cache/`.
3. Normalize query outputs into tracked canonical data.
4. Compute before/after deltas for `change` and `growth` modes.
5. Promote approved outputs into `data/` and update run registry.

## Tooling

- Python orchestration uses `uv`.
- Formatting/linting uses `ruff`.
- The extractor engine remains the Rust tool at `../iso-26262-coding-standard-extraction`.

## Common Commands

```bash
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run python scripts/bootstrap_session.py
```
