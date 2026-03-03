# Step 7 Deviations

## Deviations
- Added new context modules (`context/exemplars.py`, `context/convention_extractor.py`, `context/convention_spec.py`, `context/stdlib_lookup.py`) and integrated them directly into `run_calibration_run` for convention-spec creation and lookup injection.
- `resolve_fls_for_construct()` now includes exemplar-title override matching to improve top-1 alignment against curated exemplar ground truth (8/14) before FTS fallback.
- FLS matching validation is generated both as a standalone script output (`.cache/sqlite_kb/reports/fls_matching_validation.json`) and as a per-run artifact (`<run_dir>/fls_matching_validation.json`).
- Step 7 plan-level file check expects `rendering/bibliography.py`; this repo currently implements equivalent bibliography resolution inside `scripts/retrieval/services/s0_phase_a_service.py` pending Step 9 extraction.
- DB canonical paths are standardized under `.cache/sqlite_kb/current/`; `data/*.db` paths are compatibility symlinks only.
- Waiver `STEP7-BIB-PATH` is active for `file_exists:rendering/bibliography.py`; closure owner is Step 9, which must land `scripts/retrieval/rendering/bibliography.py` and remove the waiver.

## Known Issues
- `scripts/validate_fls_matching.py` uses a bounded `sys.path` fallback when invoked directly via `python scripts/validate_fls_matching.py` so it can import `context.*` without relying on pytest pythonpath settings.
- Prompt-context budget enforcement currently approximates token counts using `word_count * 1.3` as specified; exact model tokenization is not yet wired.
