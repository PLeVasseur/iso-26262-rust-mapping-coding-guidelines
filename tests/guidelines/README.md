# Guideline Fixtures

Create rule-specific fixture folders as:

`tests/guidelines/<RULE_ID>/`

Each rule folder should include:

- `metadata.yaml`
- mode-specific fixture files (`auto/`, `audit/`, `hybrid/` as applicable)
- expected outputs/findings for deterministic verification

Use the scaffold helper to generate/update pilot fixtures from the backlog:

```bash
uv run python scripts/scaffold_guideline_fixtures.py
```
