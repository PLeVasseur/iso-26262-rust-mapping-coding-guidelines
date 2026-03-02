# Step 2 Deviations

## Deviations
- Implemented a standalone renderer (`scripts/rendering_v2/rst_renderer.py`) rather than delegating to upstream `guideline_rst_template()` directly; this keeps deterministic control over Step 2-specific fields (`:edition:`, miri floor behavior, URL fallback, citation map artifact).
- Added `guideline_manifest.json` as an extra traceability artifact in `rerendered_rst/` in addition to the requested `rerender_manifest.json` and `citation_key_map.json`.
- Added unit tests in `tests/unit/test_rendering_v2.py` for deterministic ID generation and artifact rerender integration.

## Known Issues
- `fls` remains a deterministic placeholder (`fls_<sha12>`) until Step 7 provides authoritative FLS paragraph resolution.
- A full 14-exemplar roundtrip conformance test is not yet implemented in this step; current tests cover renderer mechanics and rerender integration only.
