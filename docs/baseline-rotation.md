# Baseline Rotation

Baseline rotations are controlled updates to toolchain/docs/enforcement baselines.

## Rotation checklist

1. Update toolchain version (`rust-toolchain.toml`).
2. Refresh rust docs snapshot metadata:

   ```bash
   uv run python scripts/snapshot_rust_docs.py --toolchain <toolchain>
   ```

3. Re-run orchestration in `full` profile.
4. Review before/after diff artifacts in `.cache/ops/diffs/`.
5. Update `data/run_registry.yaml` only after review sign-off.

## Evidence

- Run summary and metrics artifacts
- Updated lint/tool inventories
- Any extractor findings created during rotation
