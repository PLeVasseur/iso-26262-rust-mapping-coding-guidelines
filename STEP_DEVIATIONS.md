# Step 9 Deviations

## Deviations
- Canonical modules now live under `scripts/retrieval/*`; legacy `scripts/rendering_v2`, `scripts/judges_v2`, and `scripts/validation_v2` have been removed as part of shim-kill consolidation.
- Added explicit basename aliases in `scripts/step_orchestrator.py` so prerequisite resolution remains deterministic after the v2-to-canonical path migration.
- Updated unit coverage to validate canonical import paths and zero `_v2` dependency references.

## Known Issues
- Runtime entrypoint has been reduced to a thin delegator in `scripts/retrieval/services/s0_phase_a_service.py`; remaining dense implementation now resides in `scripts/retrieval/services/s0_phase_a_impl.py` and still requires deeper decomposition into domain modules.
- Full post-extraction equivalence execution (`extraction_baseline` vs `post_extraction`) was not re-run against a freshly generated post-recovery run-id; existing baseline compare used the current `phase_a_opencode_v3_exec2` artifacts.

## Waivers
- Waiver `STEP7-BIB-PATH` is active while extraction wiring is in progress.
- Linked DoD check: `file_exists:rendering/bibliography.py`.
