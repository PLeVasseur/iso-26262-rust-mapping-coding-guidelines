# Step Deviations

## Deviations

- Plan files are loaded from `$OPENCODE_CONFIG_DIR/plans/v17_2_plan` instead of `plans/v16` inside this repository, because the authoritative plan lives in the OpenCode config directory and this repository has no local plan files.
- `scripts/step_orchestrator.py` uses v17.2 step range `0..14` and v17.2 dependency graph from `OVERVIEW.md` instead of the legacy v16 `0..15` graph embedded in the step draft.
- OpenCode health verification uses `/global/health` as the canonical machine endpoint; `/health` returns the web UI shell in this OpenCode version.

## Known Issues

- None.
