# Judge Calibration on Known-Bad RST (Step 4 Part E)

Calibration artifacts:

- `.cache/sqlite_kb/reports/s0_phase_a_20260227_v8_execution/judge_calibration_report.json`
- `.cache/sqlite_kb/reports/s0_phase_a_20260227_v8_execution/judge_calibration_bad_rst_results.json`

## Exemplar Calibration Gate

- Result: `calibration_passed: true`
- Judges: `technical_accuracy`, `functional_safety_relevance`, `pedagogical_quality`
- Exemplars evaluated: 4
- Total calls: 12

## Known-Bad vs Renderer-Fixed Comparison

Bad corpus:

- `.cache/sqlite_kb/reports/phase_a_opencode_v3_exec2/generated_guidelines_rst/*.rst`

Good corpus:

- `.cache/sqlite_kb/reports/s0_phase_a_20260227_v8_execution/rerendered_rst/*.rst`

Observed results:

- Mechanical failures detected: 4
- Content failures remaining after renderer fix: 0
- All 4 mechanical failures were `technical_accuracy` checks with reason code:
  - `technical_ids_not_fabricated_failed`

Per-target mechanical transitions (`fail -> pass` after renderer fix):

- `CORE_CONC_003`
- `CORE_SAFE_003`
- `RET_ISSUE_005`
- `RET_RESOLVE_008`

## Interpretation

- Judges now produce target-specific, non-empty reason codes on known-bad RST.
- Renderer-fixed outputs clear the detected mechanical defect class.
- Remaining quality work is shifted to downstream writer improvements rather than renderer mechanics for this batch.
