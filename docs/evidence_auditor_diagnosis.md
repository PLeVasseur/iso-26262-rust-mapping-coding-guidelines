# Evidence Auditor Diagnosis (Step 4 Part A)

## Scope

- Source run analyzed: `.cache/sqlite_kb/reports/phase_a_opencode_v3_exec2`
- Non-abstain targets analyzed: `CORE-CONC-003`, `CORE-SAFE-003`, `RET-ISSUE-005`, `RET-RESOLVE-008`
- Primary artifacts:
  - `stage_b_judges/evidence_auditor/*.json`
  - `judge_aggregate.json`
  - `drafts.jsonl`
  - `writer_subagent_outputs/evidence_synthesizer.jsonl`
  - `generated_guidelines_rst/*.rst`
  - `rerendered_rst/*.rst`

## Findings

1. The old evidence auditor receives draft JSON, not rendered RST.
   - The monolith judge prompt embeds `Draft context JSON` (`scripts/retrieval/services/s0_phase_a_service.py:2852`).
   - This prevents direct quality evaluation of what readers actually consume.

2. The failure signal is prompt-quality floor behavior, not transport failure.
   - All evidence auditor invocations were transport `ok` (`stage_b_judge_invocations.json`).
   - Failing targets (`RET-ISSUE-005`, `RET-RESOLVE-008`) record:
     - `decision: fail`
     - `reason_codes: ["judge_output_quality_floor_failed"]`
     - `summary: "(missing specific target reference)"`
   - Passing targets (`CORE-CONC-003`, `CORE-SAFE-003`) show empty summaries and no reason codes but were accepted as `pass`.

3. Fabricated IDs are present in both pass and fail cases.
   - Example pass case: `generated_guidelines_rst/core-conc-003.rst` uses `:id: gui_b1a1c4a4ee36`.
   - Example fail case: `generated_guidelines_rst/ret-issue-005.rst` uses `:id: gui_875602d8782f`.
   - Since both classes contain fabricated IDs, fabricated IDs are not the discriminating root cause for the evidence auditor fail split.

4. Re-run outcome using rendered-RST judges confirms modality issue.
   - After Step 2 rerendering, standalone judges on `rerendered_rst/` produced non-empty reasoned outputs and stable binary verdicts in
     `.cache/sqlite_kb/reports/s0_phase_a_20260227_v8_execution/standalone_judge_aggregate.json`.
   - Known-bad calibration shows bad RST fails and rerendered RST passes for technical checks in
     `.cache/sqlite_kb/reports/s0_phase_a_20260227_v8_execution/judge_calibration_bad_rst_results.json`.

## Root Cause

Primary root cause: **judge prompt/input deficiency**.

- The legacy evidence auditor is calibrated for draft JSON payloads and enforces a quality floor only on fail responses, yielding inconsistent behavior.
- It does not evaluate final rendered artifacts, and its summaries/reason codes are often non-actionable.

Secondary contributors:

- Mixed old judge set (6 judges, 3 frequent abstainers) obscures decision quality.
- Mechanical renderer defects existed in exec2 output, but those defects alone do not explain pass/fail split inside the old evidence auditor.

## Decision

**Replace** the old evidence-auditor-centered Stage-B path with the standalone Step 4 v2 three-judge pipeline operating on rendered RST.

Rationale:

- Produces non-abstain, actionable outputs.
- Aligns evaluation target with reader-visible content.
- Removes unstable quality-floor dependence tied to draft-JSON prompt structure.
