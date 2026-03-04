# STEP7-BIB-PATH Waiver Closure Note

- Date: 2026-03-04
- Waiver token: `STEP7-BIB-PATH`
- Linked DoD check: `file_exists:rendering/bibliography.py`

Closure actions completed:

1. Removed active waiver declaration from `STEP_DEVIATIONS.md`.
2. Removed waiver mapping in `scripts/step_orchestrator.py` (`STEP_WAIVER_RULES`).
3. Updated orchestrator unit coverage by inverting `test_step8_bibliography_waiver_rule_active` behavior to closure-state validation (`test_step8_bibliography_waiver_rule_closed`).

Result: bibliography path waiver is fully closed and no longer required for Step 9 compliance.
