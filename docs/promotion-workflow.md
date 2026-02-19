# Promotion Workflow

Use this workflow to move generated artifacts from a successful run into an accepted operational baseline.

## 1) Run and capture evidence

Run orchestration through the bootstrap wrapper:

```bash
uv run python scripts/bootstrap_session.py --profile quick
```

Record the `run_id` from command output and keep these artifacts for review:

- `.cache/ops/runs/<run_id>/summary.md`
- `.cache/ops/runs/<run_id>/metrics.json`
- `.cache/ops/runs/<run_id>/run_manifest.json`
- `.cache/ops/runs/<run_id>/promotion_candidate.json`

Build a diffset and open local HTML review:

```bash
uv run python scripts/review_diffset.py --after-run <run_id>
```

Export reviewer feedback from the UI to `feedback/diffset_reviews/<diffset_id>.yaml`.

## 2) Validate policy gates

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

If any gate fails, resolve the issue and re-run orchestration before requesting promotion.

## 3) Reviewer sign-off

Promotion requires explicit reviewer sign-off for:

- updated guideline/backlog artifacts,
- compliant/non-compliant examples with rationale/amplification/exceptions,
- traceability completeness,
- licensing-guard pass,
- no unresolved diffset `block` review items,
- no unresolved S0/S1 extractor findings for affected areas.

Enforce blocker gate from CLI:

```bash
uv run python scripts/check_diffset_review_gate.py --diffset-id <diffset_id>
```

## 4) Update run registry

After approval, update `data/run_registry.yaml` with an `accepted_runs` entry:

- `corpus_pack_id`
- `mode`
- `accepted_run_id`
- `scope_fingerprint`
- `accepted_at`
- `accepted_by`

The `scope_fingerprint` should match the value in `.cache/ops/runs/<run_id>/metrics.json`.

## 5) Commit promotion changes

Commit only durable artifacts and docs. Keep ephemeral run artifacts under `.cache/` out of commits.

Suggested commit type for accepted baseline updates:

- `chore(baseline): register accepted run <run_id>`
