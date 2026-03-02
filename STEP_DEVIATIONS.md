# Step 3 Deviations

## Deviations
- Added a standalone validation package (`scripts/validation_v2/`) and runner that reads `rerendered_rst/` artifacts and writes `output_conformance_report.json`; monolith and go/no-go wiring remain deferred to Step 9 as required.
- Calibrated conformance severity to avoid exemplar false positives: missing `:edition:`, missing `:miri:` on `unsafe`, and missing bibliography marker are currently `warning` severity because these patterns appear in curated exemplars.
- Updated `scripts/integration_checkpoint.py` CP-A to validate standalone artifacts (`rerendered_rst/`, `output_conformance_report.json`, `standalone_judge_aggregate.json`) while preserving monolith evidence/citation regression checks.

## Known Issues
- Conformance currently fails Step 2 rerendered outputs on `fls_id_looks_like_hash`; this is expected until Step 7 introduces authoritative FLS lookup and replacement.
- docutils parsing emits upstream deprecation warnings during tests (`OptionParser`, `Node.traverse`); behavior is correct, but warnings remain until a later cleanup.
