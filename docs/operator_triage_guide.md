# Operator Triage Guide

## Step 2 Renderer Triage

- Confirm `rerendered_rst/` was created under the selected run directory.
- Compare one file in `rerendered_rst/` with its counterpart in
  `generated_guidelines_rst/`; verify content is equivalent and mechanical
  formatting is corrected (ID format, prefixes, `:edition:`, bibliography URL
  fallback).
- Verify `rerender_manifest.json` includes `draft_id`, `prompt_id`, `file`, and
  `output_path` for each non-abstain draft.
- Verify `citation_key_map.json` exists and declares
  `citation_placement_policy: "renderer_injected"`.
- Re-run the re-renderer for the same run directory and confirm deterministic
  guideline IDs (same IDs across both runs).
- Confirm monolith source `scripts/retrieval/services/s0_phase_a_service.py` is
  unchanged.
