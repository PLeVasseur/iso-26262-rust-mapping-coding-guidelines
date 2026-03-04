# Step 9 Deviations

## Deviations
- Canonical modules now live under `scripts/retrieval/*`; legacy `scripts/rendering_v2`, `scripts/judges_v2`, and `scripts/validation_v2` have been removed as part of shim-kill consolidation.
- Added explicit basename aliases in `scripts/step_orchestrator.py` so prerequisite resolution remains deterministic after the v2-to-canonical path migration.
- Updated unit coverage to validate canonical import paths and zero `_v2` dependency references.

## Known Issues
- Runtime entrypoint has been reduced to a thin delegator in `scripts/retrieval/services/s0_phase_a_service.py`; retained implementation surface in `scripts/retrieval/services/s0_phase_a_impl.py` remains intentionally soft-retired compatibility code.

## Phase-A Helper Liveness Matrix

| Module | Classification | Disposition |
|---|---|---|
| `scripts/retrieval/services/phase_a_calibration.py` | `compat_forwarder` | Retain as compatibility import facade; all exports delegate to `phase_a_retired` unsupported-operation handlers. |
| `scripts/retrieval/services/phase_a_calibration_support.py` | `dead_unreferenced_code` | Defer removal. File is currently unreferenced by active command dispatch and retained temporarily for forensic parity/reference during recovery closure. Target retirement step: Step 15 cleanup. |
| `scripts/retrieval/services/phase_a_doctor.py` | `retired_stub` | Retain as explicit soft-retire shim that forwards to `phase_a_retired.run_doctor`. |
| `scripts/retrieval/services/phase_a_targets.py` | `retired_stub` | Retain as explicit soft-retire shim; `_run_eval_for_corpus` hard-fails to prevent accidental runtime reuse. |
| `scripts/retrieval/services/phase_a_writer_reports.py` | `retired_stub` | Retain as explicit soft-retire guard; `emit_tail_reports` raises at runtime to block retired flow reuse. |

## Recovery Decomposition Gap Disposition Matrix

| Gap | Decision | Reason | Owner | Trigger Condition | Planned Target Step |
|---|---|---|---|---|---|
| `scripts/retrieval/operations/query.py` remains implementation-heavy | `defer` | Current query behavior is stable; decomposition would mix structural refactor with active retrieval tuning scope. | Retrieval maintainers | Any Step 10+ change touching query routing internals or >100 LOC net-new query logic | Step 15 |
| `scripts/retrieval/services/guidelines_repo_service.py` remains large | `defer` | Active autopilot and doctor paths are still being iterated; split now would increase merge risk mid-stream. | Guidelines-repo maintainers | New feature touching both doctor and bootstrap paths in same PR, or file exceeds 650 LOC | Step 15 |
| `scripts/retrieval/materialize/dedupe.py` not created | `defer` | Dedupe logic remains co-located with persistence/operation code; extraction is non-blocking for current deliverables. | Retrieval maintainers | Materialize dedupe behavior change or repeat edits across 3+ call sites | Step 15 |
| `scripts/retrieval/materialize/corpus.py` not created | `defer` | Corpus-specific materialize branching remains manageable in existing modules. | Retrieval maintainers | Addition of a new corpus-specific materialize branch or test-only forks in operation layer | Step 15 |

## Waivers

- None active.
