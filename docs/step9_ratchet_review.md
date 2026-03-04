# Step 9 Ratchet Review

## Threshold Ratchet Decision
- Decision: held
- Rationale: Step 9 recovery focused on code extraction and shim removal; no new calibration cohort was collected. Existing signal is insufficient for a safe threshold raise.
- Confidence basis: `N=5` drafts in `judge_aggregate.json` (`4` non-abstain, `1` abstain), with `3` Stage-B judges per non-abstain target in the consolidated runtime path.
- Observed rates (current): candidate grade `0/4`, blocked/review `4/4`, abstain rate `0.20`.

## Prompt Discrimination Iteration
- Technical accuracy:
  - Baseline (Step 4): pass-rate `0.50` on known-positive slice.
  - Current (Step 9 recovery comparison set): pass-rate `0.50`.
  - Delta: `0.00` (held; no prompt-token edits).
- Functional safety relevance:
  - Baseline (Step 4): pass-rate `1.00` on known-positive slice.
  - Current (Step 9 recovery comparison set): pass-rate `1.00`.
  - Delta: `0.00` (held; no prompt-token edits).
- Observed signal movement: none; this step is extraction/integration, not new calibration collection.

## Per-Judge Discrimination Summary
- technical_accuracy: pass=`2`, fail=`2`, abstain=`0`, pass-rate=`0.50`, positive-vs-known-bad delta held.
- functional_safety_relevance: pass=`4`, fail=`0`, abstain=`0`, pass-rate=`1.00`, positive-vs-known-bad delta held.
- pedagogical_quality: pass=`3`, fail=`1`, abstain=`0`, pass-rate=`0.75`, positive-vs-known-bad delta held.

## Notes
- Source artifacts: `.cache/sqlite_kb/reports/phase_a_opencode_v3_exec2/judge_aggregate.json`, `.cache/sqlite_kb/reports/phase_a_opencode_v3_exec2/standalone_judge_aggregate.json`.
- Prompt contract snapshot used: `.cache/sqlite_kb/reports/phase_a_opencode_v3_exec2/writer_subagent_outputs/prompt_contract_snapshot.json`.
- Next ratchet action: collect an expanded post-recovery calibration sample before raising thresholds.
