# Step 5 Deviations

## Deviations
- Runtime target definitions are currently sourced from `data/query_testsets/*.yaml` in `run_enumerate_targets`; this step therefore staged new targets in both query testsets and `config/s0/s0_targets.yaml` metadata.
- The step draft uses `scripts/sqlite_query.py --query ... --output-json`; current CLI uses `--query-text` and already emits JSON for retrieval modes, so coverage checks were executed with `--query-text`.
- The step draft assumes per-target calibration invocation via `--targets`; current `scripts/sqlite_kb.py calibration-run` does not expose this option and enforces a 5-target bootstrap subset.

## Known Issues
- Full `calibration-run` evidence synthesis on custom expanded subsets (`target_expansion_v17_newtargets2`, `target_expansion_v17_newtargets3`) exceeded practical step runtime limits; coverage validation completed, but evidence gate confirmation for the expanded subset remains pending.
- The legacy monolith still hard-codes a 5-target bootstrap selection in `run_calibration_run`; expanded target definitions are staged for downstream integration but not yet consumed by the default bootstrap selection path.
