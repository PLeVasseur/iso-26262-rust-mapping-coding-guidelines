# Guideline Record Spec (v3)

This document defines the canonical guideline record structure in `data/todo_guidelines.yaml`.

## Core fields

- `id`: stable guideline identifier (`RG-...`).
- `category`: MISRA-style classification (`Mandatory|Required|Advisory|Disapplied`).
- `technical_topic`: domain/topic bucket used for taxonomy and grouping.
- `rule_statement`: short rule title/statement.
- `amplification`: detail section that clarifies how to apply the rule.
- `exceptions`: section defining when the rule may not apply.
- `rationale`: why the rule exists.
- `iso_seeds`: source ISO seed IDs.
- `fls_refs`: one or more mapped FLS paragraph references (`fls_*`).
- `rule_family_id`: optional stable grouping ID for decomposed siblings.
- `decomposition_parent`: optional parent guideline ID when split from a broader rule.
- `obligation_units`: optional normalized obligation unit IDs linked from `seed_topics`.
- `scope`: required analysis boundary (`system|crate|module`).
- `decidable`: `decidable|undecidable`.
- `decidability_rationale`: reason for decidability classification.

## Conditional decidability fields

- If `decidable = undecidable`:
  - `decidable_status` must be absent.
  - `clippy_lint_id`, `clippy_lint_url`, `clippy_candidate_tracker` must be absent.

- If `decidable = decidable`:
  - `decidable_status` is required and must be one of:
    - `compiler`
    - `clippy`
    - `possible-with-clippy`
    - `impossible-with-clippy`

- If `decidable_status = clippy`:
  - `clippy_lint_id` required (existing stable lint id).
  - `clippy_lint_url` required (stable index URL).

- If `decidable_status = possible-with-clippy`:
  - `clippy_candidate_tracker` required (issue/tracker link).

## Lifecycle/enforcement fields

- `state`: `DRAFT|TRIAL|ENFORCED|DEPRECATED`.
- `enforcement_mode`: `AUTO|AUDIT|HYBRID`.
- `enforcement_details`: explanatory details for enforcement mode.
- `evidence_artifacts`: list of evidence file paths.
- `deviation_requirements`: required deviation process details.

## Required example structure

Each guideline must include:

- `examples.compliant`
- `examples.non_compliant`

Each example object must include:

- `code_path`
- `doc_path`
- `explanation`
- `compile_expectation`

Allowed compile expectations:

- Compliant: `compile_pass|no_run|documented-only`
- Non-compliant: `compile_fail|compile_pass|documented-only`

Compiler-status default rule:

- For `decidable_status = compiler`, non-compliant example must default to `compile_fail`.
- If overridden, `examples.non_compliant.expectation_exception_reason` is required.

## Example file conventions

- Markdown files (`*.md`) are the source-of-truth examples and must include:
  - prose explanation
  - at least one Rust fenced code block
- Rust source files (`*.rs`) are convenience extracted files for tool/harness checks.

## Validation commands

```bash
uv run python scripts/validate_schemas.py --strict-generated
uv run python scripts/check_guideline_completeness.py
uv run python scripts/check_guideline_examples.py
uv run python scripts/check_rule_decomposition.py
uv run python scripts/check_fls_proxy_coverage.py
uv run python scripts/check_guideline_quality.py
```
