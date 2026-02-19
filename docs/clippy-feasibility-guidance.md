# Clippy Feasibility Guidance

Use this guidance when assigning `decidable_status` for `decidable` guidelines.

## Status meanings

- `compiler`: rule can be decided by compiler diagnostics.
- `clippy`: rule can be decided by an existing Clippy lint today.
- `possible-with-clippy`: rule appears feasible for future Clippy lint implementation but does not exist yet.
- `impossible-with-clippy`: rule is decidable in principle but not realistically expressible as a Clippy lint.

## Required evidence by status

- `clippy`:
  - set `clippy_lint_id`
  - set `clippy_lint_url`
  - reference must exist in `data/clippy_lints_catalog.yaml`

- `possible-with-clippy`:
  - set `clippy_candidate_tracker` (issue/tracker link)
  - include rationale explaining expected lint shape and constraints

- `impossible-with-clippy`:
  - explain why Clippy architecture is insufficient (cross-program context, dynamic/runtime requirements, etc.)
  - specify alternate enforcement route (audit/tool/process)

## Stable index source

Use the stable lint index as canonical source for existing lint references:

- https://rust-lang.github.io/rust-clippy/stable/index.html

Refresh tracked catalog snapshot with:

```bash
uv run python scripts/update_clippy_lints_catalog.py
```

Check for drift:

```bash
uv run python scripts/update_clippy_lints_catalog.py --check
```
