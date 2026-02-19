# Guideline Fixtures

Create rule-specific fixture folders as:

`tests/guidelines/<RULE_ID>/`

Each rule folder should include:

- `metadata.yaml`
- `examples/compliant.md` and `examples/non_compliant.md` as canonical rustdoc-testable docs
- `examples/compliant.rs` and `examples/non_compliant.rs` as convenience extracted sources
- mode-specific fixture files (`auto/`, `audit/`, `hybrid/` as applicable)
- expected outputs/findings for deterministic verification

Markdown example files are source-of-truth and must contain:

- prose explanation of why the example is compliant/non-compliant
- at least one Rust fenced block (`rust`, `no_run`, `compile_fail`, etc.)

Use the scaffold helper to generate/update pilot fixtures from the backlog:

```bash
uv run python scripts/scaffold_guideline_fixtures.py
```

Run example compile/lint checks:

```bash
uv run python scripts/check_guideline_examples.py
```
