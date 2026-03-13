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
- Confirm `.cache/convention_spec.json` exists and `guidelines_repo_commit_sha`
  matches the currently pinned upstream commit.
- If `<run_dir>/convention_spec_diff.json` exists, inspect `keys_changed` and
  ensure changes are expected for the upstream commit transition.
- Spot-check lookup coverage in `<run_dir>/lookup_status.json`:
  - `stdlib_entries` should be non-zero and `stdlib_source` should be
    `core_docs_db` when `.cache/sqlite_kb/current/core_docs.sqlite` is present
    (or `data/core_docs.db` compatibility symlink resolves to that target).
  - `fls_spec_db.available` should be `true` with non-zero paragraph count.
- If Step 7 reports `file_exists:rendering/bibliography.py` as fail, treat it as
  a plan-path mismatch unless bibliography resolution behavior is also missing.
- Inspect `writer_subagent_outputs/subagent_invocation_trace.json` and verify
  each invocation has `injected_context` budgets recorded.
- Run `uv run python scripts/validate_fls_ws7.py --run-dir <run_dir>` and confirm
  `<run_dir>/ws7_validation.json` exists, reports `runtime_mode:
  ws7_staged_retrieval_v1`, and records stage artifacts / candidate traces for
  the validated atoms.
- Treat `scripts/validate_fls_matching.py` as a compatibility wrapper only; do
  not treat grounding-only abstention language as the active FLS runtime truth.

## WS7 Prework Operational Checks

- Treat incremental refresh as the default build path for source-driven corpora.
- Use `--no-incremental` only when deliberately forcing full rebuild semantics.
- Use `--force-rebuild` only when an incremental run fails invariants and you are
  explicitly authorizing fallback replacement of the live DB.
- Treat `.cache/sqlite_kb/reports/<corpus>/current_chunk_first_validation.json` as the
  operational chunk-first health surface for `fls_spec`, `core_docs`, and
  `rust_reference`.
- Confirm each current report includes non-empty `db_path`, `db_sha256`,
  `latest_migration_id`, and `schema_user_version`, plus passing
  `chunk_fts_mapping` diagnostics.
- If `sqlite_kb migrate` or corpus build workflows have been run recently, expect
  those current reports to refresh automatically; do not trust older ad hoc JSON
  files in unrelated temp/report directories as current-state evidence.
- Treat `.cache/sqlite_kb/reports/ws7_prework_current/ws7_prework_closure_report.json`
  as the workflow-backed prework status packet.
- A `fail` status there is expected until the required proof JUnit artifacts are
  present; do not hand-wave it away.
- Only treat a strict closure packet such as
  `.cache/sqlite_kb/reports/ws7_prework_20260309_v3/ws7_prework_closure_report.json`
  as valid when it references the same converged current DB identities (or an
  explicitly named snapshot set) that review is certifying.
- For incremental corpus runs, inspect the operator summary under
  `.cache/sqlite_kb/reports/<corpus>/incremental/<run_id>/` and verify it points
  to all of:
  - `<corpus>_pre_apply_delta_report.json`
  - `<corpus>_post_apply_delta_report.json`
  - `<corpus>_refresh_contract.json`
  - `<corpus>_cross_db_validation.json`
  - `<corpus>_promotion_provenance.json`
  - `<corpus>_operator_summary.json`

## Step 8 Per-Role Validation + Retry Triage

- Verify `<run_dir>/role_validation_report.json` exists and includes
  `retry_variant`, `convention_retry_budget`, `per_target_retry_budget`, and
  non-empty `entries`.
- Confirm `retry_variant` follows machine rule: viable (`>= 0.50`) -> `2`
  retries, marginal (`>= 0.25`) -> `1`, not-viable (`< 0.25`) -> `0`.
- Spot-check `entries[*].attempt_entries[*].violations` and verify retry prompts
  used specific violation checks/messages (not generic "try again").
- Confirm `validation/role_validators.py` enforces exact `:cite:` syntax with
  ``:cite:`KEY``` regex and uses citation placement policy.
- Confirm `validation/role_validators.py` checks `:std:` usage against
  fully-qualified stdlib lookup entries, not presence-only markers.
- If any role ends with remaining error violations, verify corresponding target
  is `lane: diagnostic` with `diagnostic_reason: retry_exhausted` in
  `<run_dir>/guideline_manifest.json` and excluded from candidate counting.
- Check `role_validation_report.json.retry_stats.retry_rate`; if `> 0.30`,
  verify warning `retry_rate_above_30pct` is present and triage note is added to
  the run review.
