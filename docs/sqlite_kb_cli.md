# sqlite_kb Unified CLI

Use `scripts/sqlite_kb.py` as the canonical command surface for retrieval workflows.

`--corpus` is retrieval-only for single-corpus retrieval commands.

The canonical writer flow is:

```bash
uv run python scripts/sqlite_kb.py writer-targets --output <targets-manifest>
uv run python scripts/sqlite_kb.py writer-evidence --targets-manifest <targets-manifest> --corpora <corpora> --output <evidence-manifest>
uv run python scripts/sqlite_kb.py writer-run --evidence-manifest <evidence-manifest>
uv run python scripts/sqlite_kb.py writer-quality-gate --run-dir <run-dir>
uv run python scripts/sqlite_kb.py writer-review-packet --run-dir <run-dir>
uv run python scripts/sqlite_kb.py writer-publish --run-dir <run-dir>
```

Writer command contracts are intentionally split:

- `writer-targets` is corpus-free and only selects target scope.
- `writer-evidence` is the only writer-stage command that selects corpora, and it uses `--corpora`.
- `writer-run` is manifest-only and rejects corpus flags.
- Artifact-stage writer commands such as `writer-quality-gate`, `writer-review-packet`, `writer-conformance`, and `writer-publish` reject corpus flags and operate only on run artifacts.

## Command Pattern

Retrieval commands use:

```bash
uv run python scripts/sqlite_kb.py <subcommand> --corpus <corpus> -- [subcommand flags]
```

Multi-corpus writer evidence uses:

```bash
uv run python scripts/sqlite_kb.py writer-evidence --targets-manifest <targets-manifest> --corpora <corpus-a,corpus-b>
```

Supported subcommands:

- `query`
- `eval`
- `eval-report`
- `build`
- `materialize`
- `smoke`
- `capture`
- `verify`
- `validate`
- `migrate`
- `validate-audit`

## Override Precedence

Defaults resolve in this order:

1. explicit CLI flags
2. selected retrieval profile values
3. corpus config pack defaults (`config/corpora/<corpus>.yaml`)
4. global fallback defaults

Environment variables are not used for runtime defaults.

## Examples

Evaluate rust reference with known-good defaults:

```bash
uv run python scripts/sqlite_kb.py eval --corpus rust_reference
```

Evaluate with explicit overrides:

```bash
uv run python scripts/sqlite_kb.py eval --corpus rust_reference -- \
  --retrieval-profile-path config/retrieval_profiles/rust_reference_least_bad.yaml \
  --top-k 10 --candidate-limit 5000 --semantic-retries 0
```

Run materialization for core docs:

```bash
uv run python scripts/sqlite_kb.py materialize --corpus core_docs
```

Generate human-readable eval report markdown from eval artifact:

```bash
uv run python scripts/sqlite_kb.py eval-report --corpus core_docs -- \
  --eval-path .cache/sqlite_kb/reports/core_docs/phase_a/20260224T230000Z/eval.json
```

This command also emits a weak-prompt manifest JSON next to the markdown report by default.

Validate a subagent audit report contract (JSON block + markdown):

```bash
uv run python scripts/sqlite_kb.py validate-audit --corpus core_docs -- \
  --audit-report-path /path/to/retrieval-rust-reference-subagent-audit-<phase>-<candidate-id>-<timestamp>.md
```

## Canonical Verification Command

Use this command sequence as the canonical repo verification path for retrieval changes:

```bash
uv run ruff check scripts tests/unit/sqlite_kb && \
uv run python -m unittest discover -s tests/unit/sqlite_kb -p 'test_*.py' && \
uv run rg -n "Core docs coverage for ISO 26262 Table 1 row|core-docs::.*::section" scripts/retrieval/builders/core_docs_builder.py && exit 1 || true
```

The final grep guard must not match anything. If it matches, synthetic row-summary core_docs
build behavior has reappeared and the change is rejected.
