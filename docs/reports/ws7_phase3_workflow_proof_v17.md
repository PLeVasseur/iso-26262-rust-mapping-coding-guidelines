# WS7 Phase 3 Workflow Proof v17

Commands run:

```bash
uv run pytest tests/unit/sqlite_kb/test_validate_fls_ws7.py tests/unit/test_fls_ws7_runtime.py
uv run python scripts/validate_fls_ws7.py --dataset <single-item> --trace-dir .cache/sqlite_kb/runtime/logs/ws7_v17_phase3/gui_PM8Vpf7lZ51U --run-dir .cache/sqlite_kb/runtime/logs/ws7_v17_phase3/gui_PM8Vpf7lZ51U --validation-only-continuation
uv run python scripts/validate_fls_ws7.py --dataset <single-item> --trace-dir .cache/sqlite_kb/runtime/logs/ws7_v17_phase3/gui_xztNdXA2oFNC --run-dir .cache/sqlite_kb/runtime/logs/ws7_v17_phase3/gui_xztNdXA2oFNC --validation-only-continuation
```

Workflow machinery landed in:

- `scripts/validate_fls_ws7.py`
- `tests/unit/sqlite_kb/test_validate_fls_ws7.py`

Validator-backed Phase 3 outputs now include:

- grounding artifact snapshot
- canonical investigation record
- routing artifact bundle
- ranking artifact bundle
- structural artifact bundle
- triage classification and runtime-queue flag

Anchor `gui_PM8Vpf7lZ51U`

- report: `.cache/sqlite_kb/runtime/logs/ws7_v17_phase3/gui_PM8Vpf7lZ51U/ws7_validation.json`
- trace: `.cache/sqlite_kb/runtime/logs/ws7_v17_phase3/gui_PM8Vpf7lZ51U/001_gui_PM8Vpf7lZ51U.jsonl`
- outcome: `review-correct`
- observed id: `fls_59mpteeczzo`
- investigation record: `candidate_scoring_failure` / `expected_candidate_competed_but_remains_review_only`
- triage classification: `expected_abstention`

Anchor `gui_xztNdXA2oFNC`

- report: `.cache/sqlite_kb/runtime/logs/ws7_v17_phase3/gui_xztNdXA2oFNC/ws7_validation.json`
- trace: `.cache/sqlite_kb/runtime/logs/ws7_v17_phase3/gui_xztNdXA2oFNC/001_gui_xztNdXA2oFNC.jsonl`
- outcome: `unresolved-expected`
- observed id: `fls_UNRESOLVED`
- investigation record: `none` / `no_open_failure`
- triage classification: `expected_abstention`

Proof-gate interpretation:

- both priority anchors now produce dossier-style single-item investigation records
- both priority anchors now produce all three required proof classes from validator output
- the validator can distinguish runtime-queue cases from non-runtime cases without leaving rows unlabeled
- Phase 3 workflow machinery is validator-backed and stable; this is a workflow checkpoint, not final closure
