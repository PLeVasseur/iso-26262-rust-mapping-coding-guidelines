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

## Step 4 Scope + Judges Triage

- Run `uv run python scripts/validation_v2/run_scope_check.py --run-dir <run_dir>`
  and verify `<run_dir>/scope_cardinality_report.json` exists with `results`,
  `blocked_count`, and `pass_rate`.
- Spot-check scope normalization (`std::...::AtomicBool` style terms) and verify
  family mapping does not over-count unknown terms.
- Run `uv run python scripts/judges_v2/run_judges.py --run-dir <run_dir>` and
  verify `<run_dir>/standalone_judge_aggregate.json` is written.
- Confirm standalone judge aggregate has:
  - 3 judges only (`technical_accuracy`, `functional_safety_relevance`,
    `pedagogical_quality`)
  - `review_count == 0`
  - `verdict_model: "binary_pass_fail"`
  - `verdict_triage_applied: true`
- Run `uv run python scripts/validate_judge_calibration.py --run-dir <run_dir>`
  and verify `judge_calibration_report.json` has `calibration_passed: true`.
- Check `docs/evidence_auditor_diagnosis.md` for root-cause decision and ensure
  it explicitly states keep-vs-replace disposition.
- Check `docs/judge_calibration_bad_rst_results.md` and verify known-bad files
  produce actionable failure reason codes and renderer-fixed files clear
  mechanical failures.
- Confirm monolith source `scripts/retrieval/services/s0_phase_a_service.py` is
  unchanged in this step.
