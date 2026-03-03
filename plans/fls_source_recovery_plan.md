# FLS Source Path Recovery Plan

## Objective

Move fetched FLS source artifacts out of tracked `data/` into generated-cache storage and align all runtime pointers to the canonical cache path.

## Canonical Location

- Canonical FLS source path: `.cache/fls_source/current`

## Immediate Migration Steps

1. Create canonical directory parent: `.cache/fls_source/`.
2. Move existing `data/fls_source/` directory to `.cache/fls_source/current/`.
3. Remove `data/fls_source/` from tracked tree (no compatibility bridge).
4. Update script defaults and environment checks to canonical path only.

## Code Pointer Changes

- `scripts/fetch_fls_source.py`
  - Change default output directory to `.cache/fls_source/current`.
- `scripts/build_fls_db.py`
  - Change default source directory to `.cache/fls_source/current`.
- `scripts/validate_environment.py`
  - Detect source availability from canonical path only.

## Validation

1. Run targeted Step 6/FLS unit tests.
2. Run full test suite to confirm no regressions.
3. Run environment validation script and confirm FLS assets are detected.

## Rollback

1. Move `.cache/fls_source/current` back to `data/fls_source`.
2. Restore previous defaults in the three scripts above.

## Exit Criteria

- Runtime defaults no longer point to tracked `data/fls_source`.
- Existing workflows continue to pass with canonical path.
- Reviewers can package repo content without large tracked FLS source artifacts.
