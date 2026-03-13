# WS7 Phase 1 Grounding Proof v17

Commands run:

```bash
uv run pytest tests/unit/test_fls_grounding_artifact.py tests/unit/sqlite_kb/test_fls_resolution_packet.py tests/unit/sqlite_kb/test_validate_fls_grounding.py
uv run python scripts/validate_fls_grounding.py
uv run python -c "from pathlib import Path; from scripts.retrieval.writer_host.fls_calibration import build_resolution_packet_from_rst; ..."
```

Validation artifact:

- `.cache/sqlite_kb/reports/fls_spec/ws6_grounding_validation.json`

Anchor `gui_xztNdXA2oFNC`

Before:

- documents: `types-and-traits.html`, `expressions.html`, `entities-and-resolution.html`
- sections: `types-and-traits.html#impl-trait-types`, `types-and-traits.html#struct-types`, `types-and-traits.html#struct-type-representation`, `types-and-traits.html#type-parameters`, `types-and-traits.html#type-inference`
- issue: bag-of-tokens scoring collapsed the neighborhood onto generic `type`/`struct` surfaces with no explicit specificity or health metadata

After:

- documents: `types-and-traits.html`, `entities-and-resolution.html`, `expressions.html`
- sections: `types-and-traits.html#type-inference`, `types-and-traits.html#type-parameters`, `entities-and-resolution.html#type-path-resolution`, `expressions.html#type-cast-expressions`, `macros.html#derive-macros`
- specificity: `mixed_specificity`
- outcome: emitted section priors are mixed across multiple normative documents, auditable, and not glossary-dominated

Anchor `gui_PM8Vpf7lZ51U`

Before:

- documents: `expressions.html`, `types-and-traits.html`, `glossary.html`
- sections: `types-and-traits.html#type-inference`, `expressions.html#while-let-loops`, `expressions.html#type-cast-expressions`, `undefined-behavior.html#fls_ebwqh60suhin`, `types-and-traits.html#type-coercion`
- issue: generic token and glossary density distorted the cast neighborhood before runtime scoring

After:

- documents: `expressions.html`, `inline-assembly.html`, `types-and-traits.html`
- sections: `expressions.html#type-cast-expressions`, `lexical-elements.html#integer-literals`, `types-and-traits.html#integer-types`, `expressions.html#constant-expressions`, `types-and-traits.html#type-inference`
- specificity: `mixed_specificity`
- outcome: the expected cast section now leads the emitted scoped neighborhood and glossary is removed from the emitted prior surface

Proof-gate interpretation:

- phrase-bearing evidence survives as distinct grounding channels and is test-backed
- prior rows now expose `content_type`, `specificity_state`, and auditable evidence breakdowns
- weak or mixed surfaces emit diversified neighborhoods instead of glossary capture
- glossary no longer wins emitted priors from generic density alone on either anchor

Phase 1.5 gate status:

- green for grounding redesign
- remaining failures are runtime-stage concerns, not a reason to revert to query hacks or grounding heuristics
