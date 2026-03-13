# WS7 Phase 3 Held-Out Proof v17

Command run:

```bash
uv run python scripts/validate_fls_ws7.py --dataset data/fls_ws7_heldout_manifest.json --trace-dir .cache/sqlite_kb/runtime/logs/ws7_v17_phase3/heldout --run-dir .cache/sqlite_kb/runtime/logs/ws7_v17_phase3/heldout --validation-only-continuation
```

Report:

- `.cache/sqlite_kb/runtime/logs/ws7_v17_phase3/heldout/ws7_validation.json`

Held-out proof checks:

- `accepted_wrong == 0`
- `review_unexpected == 0`
- `unresolved_unexpected == 0`
- `structural_failures == 0`
- `proof_valid == true`

Outcome summary:

- `gui_ZDLZzjeOwLSU` -> `unresolved-expected`
- `gui_ot2Zt3dd6of1` -> `unresolved-expected`
- `gui_xztNdXA2oFNC` -> `unresolved-expected`
- `gui_PM8Vpf7lZ51U` -> `review-correct`

Triage summary from validator output:

- `expected_abstention`: 4
- `true_ranking_bug`: 0
- `weak_mapping`: 0
- `stale_mapping`: 0
- `corpus_gap`: 0

Blocking condition for next proof-ladder step:

- The repo contains `data/fls_ws7_heldout_manifest.json` but does not contain explicit targeted-batch manifests or the referenced full 23-target manifest.
- The held-out manifest explicitly excludes `v17.2 full 23-target batch`, so it cannot be reused as the final 23-target proof input.
- This blocks the targeted-batch and full-23 proof steps until those manifests are provided or the dossier is corrected to name a different source of truth for them.
