# ISO 26262 Rust Mapping Operationalization

This repo hosts the operational workflow for growing Rust coding guideline coverage from ISO 26262 seeds.

## Workflow

1. Resolve extractor paths and verify extractor health.
2. Run deterministic seed queries into `.cache/`.
3. Normalize query outputs into tracked canonical data (`data/seed_topics.yaml`).
4. Generate guideline artifacts from normalized seeds:
   - `data/guideline_categories.yaml`
   - `data/todo_guidelines.yaml`
   - `data/coverage_matrix.csv`
   - `data/target_scope.yaml`
5. Run traceability and licensing gates.
6. Compute before/after deltas for `change` and `growth` modes.
7. Promote approved outputs into `data/` and update run registry.

## Tooling

- Python orchestration uses `uv`.
- Formatting/linting uses `ruff`.
- The extractor engine remains the Rust tool at `../iso-26262-coding-standard-extraction`.

## Promotion Flow

1. Run a quick deterministic session:

   ```bash
   uv run python scripts/bootstrap_session.py --profile quick
   ```

2. Review run evidence in `.cache/ops/runs/<run_id>/`:
   - `summary.md`
   - `metrics.json`
   - `run_manifest.json`
   - `promotion_candidate.json`

3. Validate quality gates locally:

   ```bash
   uv run ruff format --check .
   uv run ruff check .
   uv run python scripts/validate_schemas.py
   uv run python scripts/check_traceability.py
   uv run python scripts/check_licensing_guard.py
   ```

4. After reviewer sign-off, register/refresh accepted run entries in `data/run_registry.yaml`.

See `docs/promotion-workflow.md` for detailed acceptance guidance.

## Diffset Review Loop

Use diffsets for focused, itemized review between runs.

1. Build and launch browser review from an orchestration run:

   ```bash
   uv run python scripts/review_diffset.py --after-run <run_id>
   ```

   This command serves the review page locally and opens your browser automatically.

2. In the review page, assign per-item verdicts (`accept`, `needs_change`, `block`) and comments.

3. Export tracked feedback from the page to:

   - `feedback/diffset_reviews/<diffset_id>.yaml`

4. Check unresolved blockers before promotion:

   ```bash
   uv run python scripts/check_diffset_review_gate.py --diffset-id <diffset_id>
   ```

See `docs/diffset-review.md` for full command/reference details.

## Common Commands

```bash
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run python scripts/bootstrap_session.py
uv run python scripts/review_diffset.py --after-run <run_id>
uv run python scripts/scaffold_guideline_fixtures.py
```
