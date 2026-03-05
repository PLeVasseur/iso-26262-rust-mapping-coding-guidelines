# Retrieval Threshold Review (Step 11)

- Generated: 2026-03-05T00:00:00Z
- Inputs:
  - `retrieval_improvement_baseline.json`
  - `rust_reference_eval_report_ws3_main.json`
  - `rust_reference_eval_report_ws3_adversarial.json`
  - active query artifacts in `.cache/sqlite_kb/reports/step11_human_review/`

## Threshold Decisions

### 1) `semantic_vs_lexical_mrr_delta`

- **Old location:** blocking gate behavior (historical policy intent)
- **New location:** `advisory_thresholds.semantic_vs_lexical_mrr_delta`
- **Old value:** `0.05`
- **New value:** `0.05` (value unchanged; only gate class changed)
- **Decision:** advisory, non-blocking
- **Rationale:** This metric measures modal preference, not retrieval correctness. In Step 10 rust_reference, lexical MRR exceeds semantic MRR (`0.730159` vs `0.550794`) while lexical still retrieves valid evidence for multiple prompts. Blocking on semantic-over-lexical preference would reject usable retrieval behavior without proving downstream quality gain.
- **Anti-gaming check:** no numeric threshold was relaxed. Reclassification follows the locked decision that this metric is advisory and preserves comparability.

### 2) `hybrid_vs_best_single_mrr_tolerance`

- **Value retained:** `0.01` (blocking)
- **Decision:** keep as blocking correctness guard
- **Rationale:** In Step 10 rust_reference, hybrid underperforms best-single by `-0.048678`; this is a real fusion-quality regression, not an instrumentation artifact. Keeping this threshold blocking prevents masking a known failure mode.
- **Anti-gaming check:** unchanged threshold; no relaxation performed.

### 3) Other blocking thresholds

- Core lexical/semantic/hybrid thresholds in both corpus policies are unchanged in Step 11.
- No additional threshold moved from blocking to advisory.

## Active Output Investigation

### Baseline signal (post-Step-10)

- `rust_reference`: semantic MRR `0.550794` (below target), hybrid precision `0.476190` (below exception floor `0.550`), hybrid-vs-best-single delta `-0.048678`.
- `core_docs`: semantic MRR `0.862381`, hybrid precision `0.696000`; hybrid-vs-best-single remains slightly negative but improved with selected config.

### WS3 step-11 eval pass

- Main set (`rust_reference_eval_report_ws3_main.json`): lexical remains strongest (`mrr_at_k=0.6375`, `precision_at_k=0.547024`), semantic/hybrid ran degraded in this environment.
- Adversarial set (`rust_reference_eval_report_ws3_adversarial.json`): remains non-blocking advisory signal; used to monitor abstain robustness and off-domain behavior.

## WS3 Integration Decision

- **WS3 main:** keep as secondary structured signal until semantic backend availability is stable for non-degraded semantic/hybrid runs.
- **WS3 adversarial:** advisory-only, non-blocking.
- **Promotion criteria to co-primary gate input:**
  1. two consecutive non-degraded runs with semantic/hybrid active,
  2. abstain behavior acceptable on adversarial prompts,
  3. no regression on existing CP-A outcomes.

## Human Review Tie-In

- Human judgments are recorded in `retrieval_human_review_s0.json` and grounded in active query top-k artifacts.
- The review confirms mixed quality: strong matches for some row families (for example `1h`, `1b`) and clear misses/false positives on other prompts (notably expected-abstain and unsafe-boundary prompts).

## Writer Contract Status (Step 11 Part E)

- Writer-prompt contract update is **applied** in `config/s0/writer_prompt_contracts.yaml` (canonical schema example + grounding constraint).
- Runtime effectiveness proof is **active_validated** via Step 11.5 writer host run:
  - run dir: `.cache/sqlite_kb/reports/step11_5_writer_host_llm_20260305T113441Z`
  - targets: `RET-ISSUE-005`, `RET-RESOLVE-008`
  - role generation path: `opencode run --format json` via `scripts/retrieval/writer_host/retry.py`
  - `normalization_report.json` canonical_rate: `1.0`
  - `evidence_synthesizer_gate_report.json` status: `pass` (no overreach blocks)
  - `writer_output_auditor_report.json` status: `pass` (blocked_count `0`)
- No runtime claims are made from retired writer flow.
- No Step 11 deliverable depends on retired S0/Phase-A writer execution paths.
