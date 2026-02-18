# Diffset Review Workflow

Diffsets provide a structured review package for `before_run -> after_run` changes.

## Build + review in browser

```bash
uv run python scripts/review_diffset.py --after-run <run_id>
```

Behavior:

- builds/refreshes `.cache/reviews/diffsets/diffset-<before_or_bootstrap>__<after>/`
- serves the bundle at `http://127.0.0.1:<port>/`
- opens your default browser automatically (unless `--no-open`)

Useful flags:

- `--before-run <run_id>`: override baseline run
- `--port <n>` / `--host <addr>`: server settings
- `--no-open`: do not auto-launch browser
- `--once`: build and print/open local `file://.../review.html`, then exit

## Bundle contents

Each diffset bundle includes:

- `manifest.json`
- `items.jsonl`
- `summary.md`
- `review.html`
- `review_state.json`

## Review actions

In `review.html`:

- filter by entity/change/severity
- inspect before/after/context for each item
- set per-item verdict: `accept`, `needs_change`, `block`
- add reviewer comments
- save draft state (`review_state.json`)
- export tracked feedback

Export writes tracked feedback to:

- `feedback/diffset_reviews/<diffset_id>.yaml`

## Reporting and gates

Summarize review backlog:

```bash
uv run python scripts/review_feedback_report.py
```

Fail locally when unresolved blockers remain:

```bash
uv run python scripts/review_feedback_report.py --fail-on-blockers
```

Promotion gate check:

```bash
uv run python scripts/check_diffset_review_gate.py --diffset-id <diffset_id>
```

## Carrying feedback into a new diffset

When rerunning after remediation, carry forward matching item feedback:

```bash
uv run python scripts/reconcile_diffset_feedback.py \
  --previous-diffset-id <old_id> \
  --current-diffset-id <new_id>
```
