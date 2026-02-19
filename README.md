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
8. Refresh benchmark-quality known-good pack and alignment baseline:
   - `benchmarks/known-good/manifest.yaml`
   - `benchmarks/known-good/upstream-rst/`
   - `benchmarks/known-good/markdown/`
   - `benchmarks/known-good/canonical/`
   - `benchmarks/known-good/features/baseline.json`
   - `benchmarks/known-good/reports/alignment_report.json`
9. Compute before/after deltas for `change` and `growth` modes.
10. Promote approved outputs into `data/` and update run registry.

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
uv run python scripts/check_known_good_alignment.py
uv run python scripts/check_traceability.py
uv run python scripts/check_licensing_guard.py
```

Known-good benchmark refresh pipeline:

```bash
uv run python scripts/refresh_known_good_pack.py
```

`config/alignment_policy.yaml` supports progressive controller tightening via
`controller_progression` (iteration-ramped thresholds and gate mode).

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

By default, accepted iterations create commits for controller-mutated guideline artifacts.
Disable this only for debugging:

```bash
uv run python scripts/autonomous_controller.py --session-id run-001 --no-commit-on-accept
```

Run exactly one iteration (worker mode for supervisor orchestration):

```bash
uv run python scripts/autonomous_controller.py --resume-session run-001 --single-iteration
```

Run the fresh-process supervisor loop (spawns one fresh worker per iteration):

```bash
uv run python scripts/controller_supervisor.py --session-id run-001 --max-loops 20
```

Controller artifacts are written under:

- `.cache/controller/<session_id>/state.json`
- `.cache/controller/<session_id>/dashboard.md`
- `.cache/controller/<session_id>/iterations/<n>/`
- `.cache/controller/<session_id>/handoff/handoff.json`
- `.cache/controller/<session_id>/handoff/handoff.md`
- `.cache/controller/<session_id>/handoff/lane_status.json`
- `.cache/controller/<session_id>/handoff/delta_summary.json`

Optional extractor-backed orchestration per iteration:

```bash
uv run python scripts/autonomous_controller.py --session-id run-001 --use-orchestrate --allow-bootstrap
```

Tune beam bundle search and full-pass reranking:

```bash
uv run python scripts/autonomous_controller.py --session-id run-001 --beam-width 6 --max-actions-per-bundle 3 --full-eval-top-k 2
```

Decisioning policy is configured in `config/controller_decision_policy.yaml`.
When LLM decisioning is disabled or invalid, controller selection falls back to deterministic ranking.

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
uv run python scripts/check_known_good_alignment.py
uv run python scripts/controller_supervisor.py --session-id run-001 --max-loops 5
uv run python scripts/review_diffset.py --after-run <run_id>
uv run python scripts/scaffold_guideline_fixtures.py
uv run python scripts/autonomous_controller.py --session-id run-001
uv run python scripts/refresh_known_good_pack.py
```
