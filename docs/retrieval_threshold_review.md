# Retrieval Threshold Review (Step 10 Closeout)

- Generated: 2026-03-04T23:10:30Z
- Run directory: `.cache/sqlite_kb/reports/step10_retrieval_recovery_20260304T192658Z`
- Overall decision: **STOP**

## Snapshot

- Reports analyzed: 14
- Baseline+pool matrix (80/120/160) completed for both corpora.
- Additional fallback variants evaluated: `rrf-v1`, lexical-floor, and no-profile controls.

## Per-Corpus Decision

- **rust_reference**: `stop` via `rust_reference_before_pool0_weighted_v2.json`; semantic MRR=0.550794, hybrid precision=0.476190, hybrid-vs-best-single delta=-0.048678.
- **core_docs**: `target` via `core_docs_pool80_weighted_v2_noprofile.json`; semantic MRR=0.862381, hybrid precision=0.696000, hybrid-vs-best-single delta=-0.020000.

## Findings

- Baseline+pool sweeps completed for both corpora; additional fallback variants (rrf-v1, lexical-floor, noprofile) were captured in the same run directory.
- rust_reference remains below hybrid precision exception floor and below hybrid-vs-best-single tolerance across all tested variants; no tuned variant improved over baseline.
- core_docs reaches target policy under noprofile pool80 variant (hybrid precision 0.696, hybrid-vs-best-single delta -0.020).
- Overall Step 10 Part A decision is STOP because per-corpus policy requires all corpora to satisfy target/exception, and rust_reference remains structural-failure.

## Step 10 Outcome

- Quantitative fallback policy result: **STOP**.
- `retrieval_improvement_baseline.json` has been generated with before/after metrics, tested configs, and decision rationale.
- Step 10 Part B (mode-aware rewrite + eval/query mode threading updates) is implemented; Step 11 may consume the generated baseline and this stop diagnostic.
