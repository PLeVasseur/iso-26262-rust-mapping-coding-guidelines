# Step 4 Deviations

## Deviations
- Implemented Step 4 judges as standalone modules in `scripts/judges_v2/` with direct retry-wrapper integration (`scripts.opencode_retry_wrapper`) instead of a shared `retrieval.services.llm_client` facade, because that facade is introduced in later extraction work.
- Kept all Step 4 runtime wiring standalone (`run_scope_check.py`, `run_judges.py`, `validate_judge_calibration.py`) and did not modify `scripts/retrieval/services/s0_phase_a_service.py` or `gates/go_no_go.py`, per v17.2 deferred-monolith integration lock.
- Evidence auditor re-run with renderer-fixed output was performed via standalone rendered-RST calibration artifacts (`judge_calibration_bad_rst_results.json`) rather than patching legacy evidence-auditor execution flow in the monolith.

## Known Issues
- Standalone judge runner supports `--judge-mode heuristic` for deterministic testability; this can underrepresent semantic quality compared to full LLM mode and should not be used for final quality decisions.
- `s0_gate_policy.yaml` lane policy is declared, but legacy monolith output files may not yet emit dual-lane `publishable_decision` and `diagnostic_report` sections until Step 9 wiring.
