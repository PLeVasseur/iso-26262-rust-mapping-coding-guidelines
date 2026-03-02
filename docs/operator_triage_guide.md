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
  - `judge_mode == "llm"`
  - `judge_invocation_success_rate == 1.0`
  - `llm_invocation_errors` count is `0`
  - `prompt_contract_usage_trace_present == true`
  - `review_count == 0`
  - `verdict_model: "binary_pass_fail"`
  - `verdict_triage_applied: true`
- Run `uv run python scripts/validate_judge_calibration.py --run-dir <run_dir>`
  and verify `judge_calibration_report.json` has `calibration_passed: true`.
- Check `judge_calibration_report.json` fields explicitly:
  - `sample_counts.positive_n` and `sample_counts.negative_n`
  - `confidence_mode`
  - `warnings` for low-sample confidence and degraded-sample exclusion
  - `threshold_policy.current_thresholds` vs `threshold_policy.target_thresholds`
  - `threshold_policy.ratchet_review_step` (must point to Step 9)
- Check `docs/evidence_auditor_diagnosis.md` for root-cause decision and ensure
  it explicitly states keep-vs-replace disposition.
- Check `docs/judge_calibration_bad_rst_results.md` and verify known-bad files
  produce actionable failure reason codes and renderer-fixed files clear
  mechanical failures.
- Confirm monolith source `scripts/retrieval/services/s0_phase_a_service.py` is
  unchanged in this step.

## Step 7 Context + Lookup Triage

- Verify `<run_dir>/convention_spec.json`, `<run_dir>/lookup_status.json`, and
  `<run_dir>/convention_spec_validation.json` exist and are non-empty.
- Confirm `cache/convention_spec.json` exists and `guidelines_repo_commit_sha`
  matches the currently pinned upstream commit.
- If `<run_dir>/convention_spec_diff.json` exists, inspect `keys_changed` and
  ensure changes are expected for the upstream commit transition.
- Spot-check lookup coverage in `<run_dir>/lookup_status.json`:
  - `stdlib_entries` should be non-zero and `stdlib_source` should be
    `core_docs_db` when `data/core_docs.db` is present.
  - `fls_spec_db.available` should be `true` with non-zero paragraph count.
- Inspect `writer_subagent_outputs/subagent_invocation_trace.json` and verify
  each invocation has `injected_context` budgets recorded.
- Run `uv run python scripts/validate_fls_matching.py` and confirm
  `<run_dir>/fls_matching_validation.json` has `top1_accuracy >= 7`.
