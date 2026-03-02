# Step 4 Deviations

## Deviations
- Implemented Step 4 judges as deterministic standalone evaluators in `scripts/judges_v2/stage_b.py` rather than routing live LLM calls through an `llm_client` module, because the shared Step 9 client does not exist yet and Step 4 is constrained to standalone disk-artifact processing.
- Kept all Step 4 runtime wiring standalone (`run_scope_check.py`, `run_judges.py`, `validate_judge_calibration.py`) and did not modify `scripts/retrieval/services/s0_phase_a_service.py` or `gates/go_no_go.py`, per v17.2 deferred-monolith integration lock.
- Evidence auditor re-run with renderer-fixed output was performed via standalone rendered-RST calibration artifacts (`judge_calibration_bad_rst_results.json`) rather than patching legacy evidence-auditor execution flow in the monolith.

## Known Issues
- Standalone judges currently use heuristic deterministic scoring, so semantic depth is lower than a fully prompt-driven LLM judge; this is acceptable for Step 4 signal generation and will be revisited when Step 9 integration introduces shared runtime plumbing.
- `s0_gate_policy.yaml` lane policy is declared, but legacy monolith output files may not yet emit dual-lane `publishable_decision` and `diagnostic_report` sections until Step 9 wiring.
