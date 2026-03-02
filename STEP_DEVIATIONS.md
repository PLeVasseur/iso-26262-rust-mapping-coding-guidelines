# Step 7 Deviations

## Deviations
- Added new context modules (`context/exemplars.py`, `context/convention_extractor.py`, `context/convention_spec.py`, `context/stdlib_lookup.py`) and integrated them directly into `run_calibration_run` for convention-spec creation and lookup injection.
- `resolve_fls_for_construct()` now includes exemplar-title override matching to improve top-1 alignment against curated exemplar ground truth (8/14) before FTS fallback.
- FLS matching validation is generated both as a standalone script output (`.cache/sqlite_kb/reports/fls_matching_validation.json`) and as a per-run artifact (`<run_dir>/fls_matching_validation.json`).

## Known Issues
- `scripts/validate_fls_matching.py` uses a bounded `sys.path` fallback when invoked directly via `python scripts/validate_fls_matching.py` so it can import `context.*` without relying on pytest pythonpath settings.
- Prompt-context budget enforcement currently approximates token counts using `word_count * 1.3` as specified; exact model tokenization is not yet wired.
