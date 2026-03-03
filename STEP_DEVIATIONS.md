# Step 8 Deviations

## Deviations
- Integrated Step 8 role validation and retry directly into `run_calibration_run` in `scripts/retrieval/services/s0_phase_a_service.py` using `retry_with_violations()` from `scripts/opencode_retry_wrapper.py` instead of introducing a separate extracted writer orchestrator module before Step 9.
- Added `validation/role_validators.py` with role-specific checks and wired trusted `prompt_id` dispatch from orchestrator context.
- Added `guideline_manifest.json` emission in the run directory to record lane routing (`publishable`/`diagnostic`) and `diagnostic_reason` per draft.
- Added both `convention_retry_budget` and `compilation_retry_budget` keys to `config/s0/s0_gate_policy.yaml` while retaining legacy `max_convention_retries` and `max_compilation_retries` keys for compatibility.

## Known Issues
- Fair-scheduling is enforced via per-target budget partitioning and per-role retry caps, but the role loop still executes target-by-target; it does not perform an interleaved first-pass-over-all-targets scheduler.
- Writer role invocation traces now report transport backend as `opencode_http` for retry-wrapper paths and do not include provider message IDs from CLI event streams.

## Active Waivers
- Waiver `STEP7-BIB-PATH` is active for `file_exists:rendering/bibliography.py`.
