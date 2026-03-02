# Step 5 Deviations

## Deviations
- Runtime target definitions are currently sourced from `data/query_testsets/*.yaml` in `run_enumerate_targets`; this step therefore staged new targets in both query testsets and `config/s0/s0_targets.yaml` metadata.
- The step draft uses `scripts/sqlite_query.py --query ... --output-json`; current CLI uses `--query-text` and already emits JSON for retrieval modes, so coverage checks were executed with `--query-text`.
- The step draft assumes per-target calibration invocation via `--targets`; current `scripts/sqlite_kb.py calibration-run` does not expose this option and enforces a 5-target bootstrap subset.

## Known Issues
- `target_expansion_v17_newtargets2` was resumed successfully with `--resume` on the matching fingerprint (`profile=fast`) after initial timeout; evidence synthesis and downstream gate artifacts were produced.
- Recovery patch applied to run artifacts for `draft::ref-unsafe-001` modality/category consistency (`shall` + `mandatory`) and enforcement rerun now passes.
- `CORE-ERG-005` is marked expected-abstain in `config/s0/s0_targets.yaml` to avoid repeated triage churn from known scope overlap during later bootstrap subsets.
- The legacy monolith still hard-codes a 5-target bootstrap selection in `run_calibration_run`; expanded target definitions are staged for downstream integration but not yet consumed by the default bootstrap selection path.
