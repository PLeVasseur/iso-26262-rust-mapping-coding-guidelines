# Step 10 Deviations

## Deviations
- Kept retrieval pool-size sweep evidence from both profile-driven and direct-CLI runs because profile defaults currently override explicit CLI knobs in `apply_profile_defaults`; direct-CLI runs were needed to validate pool-size/fusion fallback behavior independently.
- `scripts/retrieval/query/rewrite_rules.py` was converted into a thin compatibility wrapper over `scripts/retrieval/core/rewrite.py` to remove duplicated rewrite logic while preserving existing import surface.

## Known Issues
- Quantitative fallback remains in STOP state for `rust_reference` (`hybrid.precision_at_k` best observed 0.476 < 0.550) after pool-size, fusion, and lexical-floor variants; stop diagnostic is recorded in `retrieval_improvement_baseline.json` and `docs/retrieval_threshold_review.md`.
- CLI/profile precedence for retrieval knobs is still profile-first in eval/query entrypoints; explicit CLI values can be shadowed when a retrieval profile is supplied.
