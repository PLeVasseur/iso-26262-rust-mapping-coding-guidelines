# Operator Triage Guide

## Step 2 Renderer Triage

- Confirm `rerendered_rst/` was created under the selected run directory.
- Compare one file in `rerendered_rst/` with its counterpart in
  `generated_guidelines_rst/`; verify content is equivalent and mechanical
  formatting is corrected (ID format, prefixes, `:edition:`, bibliography URL
  fallback).
- Verify `rerender_manifest.json` includes `draft_id`, `prompt_id`, `file`, and
  `output_path` for each non-abstain draft.
- Verify `citation_key_map.json` exists and declares
  `citation_placement_policy: "renderer_injected"`.
- Re-run the re-renderer for the same run directory and confirm deterministic
  guideline IDs (same IDs across both runs).
- Confirm monolith source `scripts/retrieval/services/s0_phase_a_service.py` is
  unchanged.

## Step 3 Output Conformance Triage

- Run `uv run python scripts/validation_v2/run_conformance.py --run-dir <run_dir>`
  and verify `<run_dir>/output_conformance_report.json` is written.
- Confirm `output_conformance_report.json` includes non-empty `per_file` results
  from `rerendered_rst/` and each row contains `valid`, `violation_count`, and
  `violations`.
- Spot-check one known-bad file (for example `core-conc-003.rst`) and verify
  violations include fabricated IDs/prefixes, missing `:std:`, and FLS/citation
  issues.
- Spot-check exemplar-derived output and verify no blocking false positives from
  custom directives/roles in docutils parsing.
- Confirm monolith source `scripts/retrieval/services/s0_phase_a_service.py` is
  unchanged in this step.
