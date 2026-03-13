# Publish Export Delta Note and Changed-Files Manifest Plan

## Purpose

This plan refines the durable publish artifact workflow so reviewer-facing publish batches clearly identify which guideline files were newly created or changed by the current run.

The current publish durability fix intentionally snapshots the full exported `src/coding-guidelines` tree under the per-run publish directory. That is the correct recovery artifact, because it guarantees that generated output does not disappear after worktree cleanup. But it is still noisy for human review. A reviewer can inspect the batch, yet they must manually infer which files are the actual outputs of the current run versus preexisting files copied through the full-tree snapshot.

The goal of this plan is to preserve the full durable snapshot while adding a precise, reviewer-friendly delta note that identifies the files created or modified by the current export batch.

## Scope

This plan covers:

- export-time changed-file detection for publish batches
- run-scoped machine-readable and human-readable changed-file notes
- packet integration so the note travels with reviewer artifacts
- regression validation proving the note exists and is accurate on `1-target` and `3-target` publish runs

This plan does not cover:

- replacing the full exported snapshot with a reduced snapshot
- changing guideline authoring or rendering semantics outside export-delta reporting
- changing conformance policy except where needed to avoid polluting changed-file detection

## Problem Statement

The current durable publish snapshot is correct for recovery but incomplete for review.

### A. The snapshot contains the whole exported tree

`writer-publish` currently copies the full `src/coding-guidelines` tree from the publish worktree into:

- `.cache/sqlite_kb/reports/writer_publish/<run>/exported_guidelines/`

This means the snapshot includes:

- newly created guideline pages
- modified chapter `index.rst` files
- preexisting guideline pages that were not created by the current run

The snapshot is therefore durable but not self-explanatory.

### B. Reviewers cannot immediately tell what this run changed

The current publish report records the snapshot path and file list, but it does not distinguish:

- files newly created by this run
- files modified by this run
- exporter-touched files that were effectively unchanged
- preexisting files copied through because the whole tree was snapshotted

As a result, reviewer handoff remains higher-friction than necessary.

### C. Delta detection must avoid conformance/build noise

Conformance runs after export and may trigger additional side effects in the worktree. If changed-file classification is taken too late, the exported-batch note can become contaminated by unrelated repo changes.

Therefore the delta note must be derived from export-time state and filtered to exporter-touched paths only.

## Evidence

Relevant files:

- `scripts/retrieval/writer_host/publish.py`
- `scripts/retrieval/operations/export_rst.py`
- `scripts/retrieval/writer_host/publish_git.py`
- `scripts/retrieval/writer_host/packet.py`
- `scripts/validate_publish_persistence.py`

Observed behavior from the current implementation:

- `export_guidelines(...)` returns `generated_files`
- the publish path copies the full exported tree into `exported_guidelines/`
- the publish report records snapshot paths but not created-vs-modified classifications
- the publish reviewer packet includes the snapshot but not a dedicated changed-files note

## Desired End State

After this plan is complete:

- every publish batch still preserves the full durable exported tree
- every publish batch also includes an explicit note describing which files were created or modified by this run
- the changed-files note is available in both machine-readable and human-readable form
- delta detection is based on export-time repo state, not post-conformance side effects
- the reviewer packet includes the changed-files note alongside the full snapshot
- `1-target` and `3-target` validation runs prove that the note persists and accurately identifies run-local changes

## Design Decisions

### 1. Keep the full snapshot

The full snapshot remains the recovery artifact. The goal is not to shrink it. The goal is to annotate it.

### 2. Add both JSON and Markdown artifacts

Recommended artifacts:

- machine-readable manifest:
  - `.cache/sqlite_kb/reports/writer_publish/<run>/exported_guidelines_changes.json`
- human-readable note inside the exported snapshot:
  - `.cache/sqlite_kb/reports/writer_publish/<run>/exported_guidelines/THIS_RUN_CHANGES.md`

### 3. Use snapshot-relative paths only

All paths recorded in the changed-files note should be relative to the exported snapshot root, for example:

- `unsafety/gui_example.rst`
- `exceptions-and-errors/index.rst`

This keeps the note portable, concise, and reviewer-friendly.

### 4. Capture delta immediately after export

The changed/new classification must be captured immediately after `run_export_rst(...)` and before conformance/build validation so the note reflects the export batch itself.

### 5. Filter by exporter-touched paths

The classification should be restricted to the exporter-touched set from `export_guidelines(...).export.generated_files` rather than arbitrary worktree changes.

## Data Model Recommendation

The machine-readable manifest should contain at least:

- `run_dir`
- `publish_root`
- `snapshot_root`
- `source_worktree`
- `generated_files`
- `created_files`
- `modified_files`
- `deleted_files`
- `unchanged_generated_files`
- `counts`

Recommended counts payload:

- `generated`
- `created`
- `modified`
- `deleted`
- `unchanged_generated`

The human-readable note should include:

- a short explanation that the snapshot contains the full exported tree
- the lists of files created or modified by this run
- counts for each bucket
- a pointer back to the JSON manifest and publish report

## Execution Strategy

## Phase 1: Capture Export-Time Delta Data

### Workstream 1: Collect exporter-touched file classifications

#### Objective

Derive created/modified/deleted/unchanged classifications for exporter-touched files immediately after export.

#### Files to work in

- `scripts/retrieval/writer_host/publish.py`
- potentially `scripts/retrieval/writer_host/publish_git.py`
- new helper module if the logic becomes large enough

#### Planned changes

1. Add a helper that reads worktree status for `src/coding-guidelines/**` immediately after export.

2. Normalize `export_guidelines(...).export.generated_files` into snapshot-relative paths.

3. Filter git status results to only those exporter-touched paths.

4. Classify each exporter-touched path into one of:
   - `created_files`
   - `modified_files`
   - `deleted_files`
   - `unchanged_generated_files`

5. Recommended status interpretation:
   - untracked / added => `created_files`
   - modified => `modified_files`
   - deleted => `deleted_files`
   - exported but absent from git-delta buckets => `unchanged_generated_files`

#### Validation goals

- classification happens before conformance
- unrelated worktree noise is excluded
- chapter `index.rst` files touched by export are classified correctly

## Phase 2: Persist Reviewer-Facing Delta Notes

### Workstream 2: Write machine-readable changed-files manifest

#### Objective

Persist a stable structured manifest describing what this publish run changed.

#### Files to work in

- `scripts/retrieval/writer_host/publish.py`

#### Planned changes

1. Write:
   - `.cache/sqlite_kb/reports/writer_publish/<run>/exported_guidelines_changes.json`

2. Record manifest location and payload summary inside `writer_publish_report.json`.

3. Ensure the manifest survives both success cleanup and failure preservation paths.

#### Validation goals

- every non-dry-run publish attempt writes the JSON manifest
- the publish report points to it directly

### Workstream 3: Write human-readable note inside the snapshot

#### Objective

Give reviewers a quick note inside the exported batch itself that explains what changed.

#### Files to work in

- `scripts/retrieval/writer_host/publish.py`

#### Planned changes

1. Write:
   - `.cache/sqlite_kb/reports/writer_publish/<run>/exported_guidelines/THIS_RUN_CHANGES.md`

2. Include:
   - snapshot root explanation
   - created files list
   - modified files list
   - deleted files list if any
   - unchanged generated files count
   - pointer to `exported_guidelines_changes.json`

3. Keep the note small and directly scannable.

#### Validation goals

- a reviewer opening the snapshot root immediately sees the run-local change note
- the note explains why unrelated preexisting files are also present in the full snapshot

## Phase 3: Surface Delta Data in the Main Publish Artifacts

### Workstream 4: Extend publish report with export delta metadata

#### Objective

Make the main publish report self-describing so downstream tools can reason about this run’s changed files without scanning the snapshot tree.

#### Files to work in

- `scripts/retrieval/writer_host/publish.py`

#### Planned changes

1. Add an `export_delta` section to `writer_publish_report.json`.

2. Include:
   - manifest path
   - note path
   - counts
   - created/modified/deleted lists or summary pointers

3. Keep full snapshot metadata separate from delta metadata so both concepts remain clear.

#### Validation goals

- the publish report clearly distinguishes full snapshot from run-local delta
- tools can consume the delta directly from the publish report

### Workstream 5: Add changed-files artifacts to the publish review packet

#### Objective

Ensure the reviewer packet carries the changed-files note and manifest by default.

#### Files to work in

- `scripts/retrieval/writer_host/packet.py`
- `scripts/retrieval/services/writer_publish_service.py`

#### Planned changes

1. Ensure the packet includes:
   - `exported_guidelines_changes.json`
   - `exported_guidelines/THIS_RUN_CHANGES.md`

2. Keep the full snapshot and publish report in the packet as they are today.

3. If desired, add packet-manifest keywords or counts for changed-file artifacts.

#### Validation goals

- the packet gives reviewers both the full snapshot and the concise change note
- the manifest is present for machine consumers

## Phase 4: Regression and Small-Batch Validation

### Workstream 6: Extend regression coverage for changed-file notes

#### Objective

Prove that changed-file notes persist and accurately describe `1-target` and `3-target` publish batches.

#### Files to work in

- `scripts/validate_publish_persistence.py`
- `tests/unit/sqlite_kb/test_writer_publish_durability.py`
- additional focused tests if needed

#### Planned changes

1. Extend the persistence validation script so it verifies:
   - `exported_guidelines_changes.json` exists
   - `exported_guidelines/THIS_RUN_CHANGES.md` exists
   - the changed-files note contains the expected created/modified file paths for the run

2. In the `1-target` validation path, ensure the note clearly identifies the created/modified guideline page and any touched index files.

3. In the `3-target` validation path, ensure the note identifies all created guideline pages and touched chapter indexes across multiple chapters.

4. Keep explicit unit coverage for a true `no_changes` path so the manifest/note behavior is also defined there.

#### Validation goals

- `1-target` publish runs produce an accurate reviewer-facing delta note
- `3-target` publish runs produce an accurate reviewer-facing delta note
- `no_changes` behavior remains explicit and non-confusing

## Recommended Implementation Notes

- Prefer export-time git status collection over post-conformance diffing
- Keep the full snapshot and the delta note separate rather than trying to over-optimize the snapshot contents
- Use stable, snapshot-relative path formatting everywhere
- Make the Markdown note intentionally short so reviewers actually use it

## Acceptance Criteria

This plan is complete when all of the following are true:

- every non-dry-run publish batch writes `exported_guidelines_changes.json`
- every non-dry-run publish batch writes `exported_guidelines/THIS_RUN_CHANGES.md`
- the publish report includes a clear `export_delta` section
- the publish review packet includes both changed-files artifacts
- the delta note distinguishes newly created and modified files from the rest of the full snapshot
- `1-target` and `3-target` persistence validation proves those note artifacts persist after command completion
- unit/regression coverage locks the behavior in place

## Recommended Validation Commands

After implementation, validate with:

1. `uv run python -m pytest tests/unit/sqlite_kb/test_writer_publish_durability.py`
2. `uv run python scripts/validate_publish_persistence.py`
3. inspect the resulting changed-files note and manifest under:
   - `.cache/sqlite_kb/reports/writer_publish/<run>/exported_guidelines/THIS_RUN_CHANGES.md`
   - `.cache/sqlite_kb/reports/writer_publish/<run>/exported_guidelines_changes.json`
