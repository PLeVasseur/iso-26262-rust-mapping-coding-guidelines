# ISO 26262 Rust Mapping Operationalization

This repo hosts the operational workflow for growing Rust coding guideline coverage from ISO 26262 seeds.

## Workflow

1. Resolve extractor paths and verify extractor health.
2. Run deterministic seed queries into `.cache/`.
3. Normalize query outputs into tracked canonical data (`data/seed_topics.yaml`).
4. Build FLS proxy artifacts:
   - `data/fls_inventory.yaml`
   - `data/fls_target_candidates.yaml`
   - `data/decomposition_report.yaml`
5. Generate guideline artifacts from normalized seeds:
   - `data/guideline_categories.yaml`
   - `data/todo_guidelines.yaml`
   - `data/coverage_matrix.csv`
   - `data/target_scope.yaml`
6. Scaffold/refresh rule fixture examples under `tests/guidelines/<RULE_ID>/examples/`.
7. Run schema/completeness/example gates plus traceability, decomposition, FLS proxy, quality, and licensing checks.
8. Compute before/after deltas for `change` and `growth` modes.
9. Promote approved outputs into `data/` and update run registry.

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
   uv run python scripts/update_clippy_lints_catalog.py --check
uv run python scripts/check_guideline_completeness.py
uv run python scripts/check_guideline_examples.py
uv run python scripts/check_rule_decomposition.py
uv run python scripts/check_fls_proxy_coverage.py
uv run python scripts/check_guideline_quality.py
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

## Autonomous Convergence Controller

Run iterative autonomous improvements using the controller loop:

```bash
uv run python scripts/autonomous_controller.py --session-id run-001
```

Controller artifacts are written under:

- `.cache/controller/<session_id>/state.json`
- `.cache/controller/<session_id>/dashboard.md`
- `.cache/controller/<session_id>/iterations/<n>/`

Optional extractor-backed orchestration per iteration:

```bash
uv run python scripts/autonomous_controller.py --session-id run-001 --use-orchestrate --allow-bootstrap
```

Guideline v3 field semantics are documented in `docs/guideline-record-spec.md`.
Clippy status assignment guidance is documented in `docs/clippy-feasibility-guidance.md`.

## Common Commands

```bash
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run python scripts/bootstrap_session.py
uv run python scripts/update_clippy_lints_catalog.py --check
uv run python scripts/check_guideline_completeness.py
uv run python scripts/check_guideline_examples.py
uv run python scripts/check_rule_decomposition.py
uv run python scripts/check_fls_proxy_coverage.py
uv run python scripts/check_guideline_quality.py
uv run python scripts/review_diffset.py --after-run <run_id>
uv run python scripts/scaffold_guideline_fixtures.py
uv run python scripts/autonomous_controller.py --session-id run-001
```
