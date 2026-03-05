# Step 11 Phase 0 Coverage Triage

- Generated: 2026-03-05T15:20:00Z
- Scope: `WS3-ISSUE-004` (`1f` unsafe-boundary), `WS3-RESOLVE-005` (`1h` Send/Sync), plus off-domain abstain sanity probe.
- Artifact directory: `.cache/sqlite_kb/reports/step11_phase0_coverage/`

## Commands Executed

- `uv run python scripts/sqlite_kb.py query --corpus rust_reference -- --mode lexical --prompt-id WS3-ISSUE-004_CANON --query-text "Which unsafe boundary mistakes break invariants when unsafe fn or unsafe trait contracts are violated?" --top-k 50 --include-score-breakdown --save-response-dir .cache/sqlite_kb/reports/step11_phase0_coverage`
- `uv run python scripts/sqlite_kb.py query --corpus rust_reference -- --mode lexical --prompt-id WS3-ISSUE-004_ALT1 --query-text "unsafe fn soundness invariants obligations unsafe trait" --top-k 50 --include-score-breakdown --save-response-dir .cache/sqlite_kb/reports/step11_phase0_coverage`
- `uv run python scripts/sqlite_kb.py query --corpus rust_reference -- --mode lexical --prompt-id WS3-RESOLVE-005_CANON --query-text "Which Send and Sync constraints are used to prevent cross-thread race conditions?" --top-k 50 --include-score-breakdown --save-response-dir .cache/sqlite_kb/reports/step11_phase0_coverage`
- `uv run python scripts/sqlite_kb.py query --corpus rust_reference -- --mode lexical --prompt-id WS3-RESOLVE-005_ALT1 --query-text "Send Sync thread safety data race ownership interior mutability" --top-k 50 --include-score-breakdown --save-response-dir .cache/sqlite_kb/reports/step11_phase0_coverage`
- `uv run python scripts/sqlite_kb.py query --corpus rust_reference -- --mode lexical --prompt-id WS3-NEG-OUT-001_CANON --query-text "Which SQL index strategy should we use for monthly billing partition pruning in PostgreSQL?" --top-k 50 --include-score-breakdown --save-response-dir .cache/sqlite_kb/reports/step11_phase0_coverage`
- `uv run python scripts/sqlite_kb.py query --corpus rust_reference -- --mode lexical --prompt-id WS3-NEG-OUT-001_ALT1 --query-text "PostgreSQL partition pruning index design for billing warehouse queries" --top-k 50 --include-score-breakdown --save-response-dir .cache/sqlite_kb/reports/step11_phase0_coverage`

## Reviewed Results

Note: lexical candidate pools were much smaller than 50 for the two target prompt families, so all returned candidates were reviewed.

### WS3-ISSUE-004 (`1f`) canonical

- File: `.cache/sqlite_kb/reports/step11_phase0_coverage/20260305T151748Z__ws3-issue-004-canon__lexical.json`
- Candidates reviewed: 3
- Abstain: `true` (`LOW_CONFIDENCE_MARGIN`)
- Top anchors reviewed:
  - `reference/keywords.html#strict-keywords` -> `not_relevant`
  - `reference/syntax-index.html#keywords` -> `not_relevant`
  - `reference/syntax-index.html#patterns` -> `not_relevant`
- Viable (`relevant` or `partial`) count: 0

### WS3-ISSUE-004 (`1f`) alternate

- File: `.cache/sqlite_kb/reports/step11_phase0_coverage/20260305T151749Z__ws3-issue-004-alt1__lexical.json`
- Candidates reviewed: 11
- Abstain: `false`
- Reviewed anchors (high-signal subset):
  - `reference/unsafe-keyword.html#unsafe-trait-implementations-unsafe-impl` -> `partial`
  - `reference/items/functions.html#combining-async-and-unsafe` -> `partial`
  - `reference/types/trait-object.html#trait-objects` -> `not_relevant`
  - `reference/items/traits.html#dyn-compatibility` -> `not_relevant`
  - `reference/keywords.html#strict-keywords` -> `not_relevant`
- Viable (`relevant` or `partial`) count: 2

### WS3-RESOLVE-005 (`1h`) canonical

- File: `.cache/sqlite_kb/reports/step11_phase0_coverage/20260305T151749Z__ws3-resolve-005-canon__lexical.json`
- Candidates reviewed: 5
- Abstain: `false`
- Top anchors reviewed:
  - `reference/items/static-items.html#mutable-statics` -> `partial`
  - `reference/items/traits.html#item-visibility` -> `not_relevant`
  - `reference/items/static-items.html#static-items` -> `partial`
  - `reference/types/trait-object.html#trait-objects` -> `not_relevant`
  - `reference/attributes.html#built-in-attributes-index` -> `not_relevant`
- Viable (`relevant` or `partial`) count: 2

### WS3-RESOLVE-005 (`1h`) alternate

- File: `.cache/sqlite_kb/reports/step11_phase0_coverage/20260305T151749Z__ws3-resolve-005-alt1__lexical.json`
- Candidates reviewed: 7
- Abstain: `false`
- Reviewed anchors (high-signal subset):
  - `reference/interior-mutability.html#interior-mutability` -> `partial`
  - `reference/items/static-items.html#using-statics-or-consts` -> `partial`
  - `reference/special-types-and-traits.html#sized` -> `not_relevant`
  - `reference/items/static-items.html#static-items` -> `partial`
  - `reference/const_eval.html#constant-expressions` -> `not_relevant`
- Viable (`relevant` or `partial`) count: 3

### Off-domain abstain sanity (`WS3-NEG-OUT-001`)

- Canonical file: `.cache/sqlite_kb/reports/step11_phase0_coverage/20260305T151749Z__ws3-neg-out-001-canon__lexical.json`
  - Candidates: 4, abstain: `false` (unexpected)
- Alternate file: `.cache/sqlite_kb/reports/step11_phase0_coverage/20260305T151749Z__ws3-neg-out-001-alt1__lexical.json`
  - Candidates: 39, abstain: `false` (unexpected)
- Result: off-domain abstain leakage persists for both phrasings.

## Gate Decision

- Prompt family `WS3-ISSUE-004` (`1f`) fails the viability threshold (2 < 3) even with alternate phrasing.
- Prompt family `WS3-RESOLVE-005` (`1h`) reaches threshold only at `partial` quality with alternate phrasing; canonical remains weak and noisy.
- Combined with persistent off-domain abstain leakage, this indicates structural coverage/quality constraints for immediate-scope unsafe/send-sync categories.

Decision: **G0-B (Path B required)**.

## Rationale

- Canonical unsafe query retrieves mostly index/keyword material.
- Alternate unsafe query improves hits but still does not reach enough focused contract-level evidence.
- Send/Sync improves with alternate phrasing but remains mostly partial and dispersed.
- Off-domain negative prompts still do not abstain in lexical mode.

Proceed to Path B structural remediation, including corpus extension governance for unsafe/send-sync high-signal sources.
