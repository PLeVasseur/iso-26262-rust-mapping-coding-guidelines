# Step 9 Deviations

## Deviations
- Implemented canonical extraction packages (`scripts/retrieval/rendering`, `scripts/retrieval/judges`, `scripts/retrieval/gates`, `scripts/retrieval/validation`, `scripts/retrieval/context`) as delegated wrappers to validated Step 2/3/4 modules where practical.
- Extracted and wired utility I/O helpers into `scripts/retrieval/services/utils.py`; `s0_phase_a_service.py` now imports these helpers instead of defining them inline.
- Replaced monolith inline RST template assembly with `render_guideline_rst(RendererInput, ...)` calls and mirrored outputs into `rerendered_rst/` for direct equivalence checks.
- Added `output_conformance_report.json` generation via extracted conformance wrapper.
- Kept monolith orchestration body in place (file remains >800 lines) to avoid unbounded behavior drift during this step's integration pass.

## Known Issues
- Full monolith decomposition into a <=800-line orchestrator is not complete; additional extraction passes are still required for strict structural target compliance.
- Stage-B judge execution is still the in-file judge loop; canonical retrieval judge wrapper exists but is not yet the runtime path.
- Scope gate module wrapper exists (`scripts/retrieval/validation/scope.py`) but explicit pre-writer orchestration wiring remains to be completed.
- Ratchet/prompt discrimination reporting artifacts are not yet auto-emitted from the monolith and still require dedicated calibration reporting integration.

## Waivers
- Waiver `STEP7-BIB-PATH` is active while extraction wiring is in progress.
- Linked DoD check: `file_exists:rendering/bibliography.py`.
