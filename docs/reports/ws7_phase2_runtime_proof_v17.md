# WS7 Phase 2 Runtime Proof v17

Commands run:

```bash
uv run pytest tests/unit/test_fls_ws7_runtime.py tests/unit/sqlite_kb/test_validate_fls_ws7.py
uv run python scripts/validate_fls_ws7.py --dataset <single-anchor-manifest> --trace-dir .cache/sqlite_kb/runtime/logs/ws7_v17_phase2_trace/gui_PM8Vpf7lZ51U --validation-only-continuation
uv run python scripts/validate_fls_ws7.py --dataset <single-anchor-manifest> --trace-dir .cache/sqlite_kb/runtime/logs/ws7_v17_phase2_trace/gui_xztNdXA2oFNC --validation-only-continuation
```

Anchor `gui_PM8Vpf7lZ51U`

Before Phase 2:

- grounding routed into the cast neighborhood, but section stage still stopped locally on a weak margin story
- top scoped candidate was not glossary, but the stage had no explicit weak-scope termination rule
- full run could collapse to unresolved after broader stages discarded the scoped candidate story

After Phase 2:

- section trace carries `scope_info` with `specificity_state: mixed_specificity`
- section decision is explicitly non-terminal with reason `SCOPED_STAGE_NON_TERMINAL_WEAK_PHRASE_SUPPORT`
- phrase-aware scoring and removal of ambiguity reranking put `fls_59mpteeczzo` at the top of section candidates
- final single-item outcome is `review-correct`, which is admissible for this anchor

Trace reference:

- `.cache/sqlite_kb/runtime/logs/ws7_v17_phase2_trace/gui_PM8Vpf7lZ51U/001_gui_PM8Vpf7lZ51U.jsonl`

Anchor `gui_xztNdXA2oFNC`

Before Phase 2:

- scoped stages lacked explicit health semantics and only reported raw fallback behavior

After Phase 2:

- section and document traces carry explicit `scope_info`
- section and document both end with `SCOPED_STAGE_NO_QUALIFYING_CANDIDATES`
- full single-item outcome is `unresolved-expected`, which is admissible for this anchor

Trace reference:

- `.cache/sqlite_kb/runtime/logs/ws7_v17_phase2_trace/gui_xztNdXA2oFNC/001_gui_xztNdXA2oFNC.jsonl`

Proof-gate interpretation:

- scoped stage termination now depends on scope health plus candidate quality, not mere local presence
- glossary remains visible but cannot silently become authoritative in weak scoped stages
- phrase-aware evidence now affects candidate comparisons materially
- anchor routing, ranking, and glossary stories are traceable from runtime artifacts
