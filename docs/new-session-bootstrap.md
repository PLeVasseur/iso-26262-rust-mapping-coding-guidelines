# New Session Bootstrap

This checklist is the canonical first-run sequence for a new session.

## 1) Environment setup

```bash
uv sync --frozen
```

## 2) Path resolution and extractor readiness

```bash
uv run python scripts/check_extractor_health.py
```

If this fails, resolve path issues in `config/extractor_paths.yaml` or override with environment variables:

- `EXTRACTOR_REPO_ROOT`
- `EXTRACTOR_CACHE_ROOT`
- `EXTRACTOR_MANIFEST_DIR`

## 3) Orchestrate a quick run

```bash
uv run python scripts/bootstrap_session.py --profile quick
```

This run executes seed querying, seed normalization, guideline artifact generation,
schema validation, guideline completeness/example checks, traceability checks,
and licensing guard checks.

For strict non-bootstrap mode (requires baseline in `data/run_registry.yaml`):

```bash
uv run python scripts/bootstrap_session.py --profile quick --no-bootstrap
```

For temporary bootstrap recovery only (avoid for normal operation):

```bash
uv run python scripts/bootstrap_session.py --profile quick --allow-missing-traceability
```

## 4) Inspect artifacts

- `.cache/ops/runs/<run_id>/run_manifest.json`
- `.cache/ops/runs/<run_id>/metrics.json`
- `.cache/ops/runs/<run_id>/summary.md`
- `.cache/ops/runs/<run_id>/promotion_candidate.json`

Optional: start interactive diffset review in browser:

```bash
uv run python scripts/review_diffset.py --after-run <run_id>
```

## 5) Optional full run

```bash
uv run python scripts/orchestrate.py --mode change --profile full --corpus-pack iso-core-part6 --allow-bootstrap
```
