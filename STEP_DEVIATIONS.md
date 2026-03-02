# Step 6 Deviations

## Deviations
- FLS `:dp:` directives in current upstream source encode paragraph IDs (`fls_*`) rather than human-readable numbers (`17:1`); parser now resolves `paragraph_number` by joining against `$GUIDELINES_REPO/src/spec.lock`.
- Parsed paragraph volume is ~5,011 rows because current `spec.lock` itself contains 5,012 paragraph entries; this exceeds the older planning estimate (~1,500-2,500) but remains consistent with the pinned upstream snapshot.

## Known Issues
- None.
