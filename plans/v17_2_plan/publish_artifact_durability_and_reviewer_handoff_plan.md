# Publish Artifact Durability and Reviewer Handoff Plan

## Purpose

This plan fixes the current writer publish lifecycle so publish outputs remain inspectable, reviewer-ready, and auditable even when commit, push, or conformance steps fail.

The current behavior is operationally dangerous: `writer-publish` renders guidelines into a temporary worktree, conditionally commits and pushes them, and then removes the worktree in a `finally` block. If conformance fails, if no commit is produced, if push fails, or if the run is difficult to reconstruct later, the most useful artifacts are lost or fragmented across unstable locations.

The goal of this plan is to make publish output durable by default and to ensure that every publish attempt leaves behind enough material for review, debugging, and recovery.

## Scope

This plan covers:

- run-scoped publish reporting
- durable persistence of exported guideline `.rst` files
- worktree cleanup policy changes so failure does not destroy evidence
- explicit handling of `dry_run`, `no_changes`, conformance failure, commit failure, and push failure
- a stable publish reviewer packet artifact
- tests that lock in persistence and failure-path behavior

This plan does not cover:

- changing guideline content quality or author prompts
- changing FLS matching semantics beyond what is needed for artifact persistence
- changing the high-level writer pipeline contract outside publish/handoff behavior

## Problem Statement

The current publish flow in `scripts/retrieval/writer_host/publish.py` has three structural flaws.

### A. Exported guideline files are ephemeral

`writer-publish` creates a temporary worktree, exports generated `.rst` files into that worktree, and then removes the worktree unconditionally in `finally`.

Result:

- successful pushes may survive only as remote branches
- failed or partial runs lose the rendered files entirely
- reviewer handoff depends on external git state rather than durable local artifacts

### B. Push only happens in a narrow success corridor

Push occurs only if all of the following are true:

- not `--dry-run`
- ingest succeeds
- export succeeds
- conformance passes in publishable mode
- `git commit` creates a diff-backed commit

Result:

- conformance failure means no push
- `no diff` means no push
- push failure can strand useful exported output without a durable handoff artifact

### C. Publish reporting is not run-scoped enough

The default report output is a shared file:

- `.cache/sqlite_kb/reports/writer_publish_report.json`

Result:

- later runs can overwrite the last publish report
- it is hard to reconstruct which run produced which branch, worktree, or failure mode
- reviewer handoff is not tied to one durable per-run artifact set

## Evidence

Relevant files:

- `scripts/retrieval/writer_host/publish.py`
- `scripts/retrieval/writer_host/publish_git.py`
- `scripts/retrieval/services/writer_publish_service.py`
- `scripts/retrieval/writer_host/publish_mapping.py`
- `scripts/retrieval/writer_host/publish_ingest.py`
- `scripts/retrieval/writer_host/packet.py`
- `scripts/retrieval/operations/export_rst.py`

Key observed behavior from code:

- temp worktree created via `create_worktree(...)`
- exported files written into guidelines repo worktree
- conformance failure returns before commit/push
- push only occurs when `commit["committed"]` is true
- worktree removed in `finally`
- default report path is global, not per-run

## Desired End State

After this plan is complete:

- every publish attempt leaves behind a run-scoped durable report
- every non-dry-run publish attempt preserves exported guideline files in a stable cache location
- failure does not destroy the generated output needed for debugging or review
- push status, commit status, branch, worktree path, and failure code are always explicit
- a reviewer can inspect one stable publish packet without needing the temporary worktree
- `no_changes` is treated as a first-class outcome rather than an ambiguous non-push case

## Execution Strategy

## Phase 1: Make Publish Artifacts Durable

### Workstream 1: Make publish reports run-scoped

#### Objective

Ensure every publish run writes its report under its own run directory instead of a shared global report path.

#### Files to work in

- `scripts/retrieval/services/writer_publish_service.py`
- `scripts/retrieval/writer_host/publish.py`
- tests for publish service behavior

#### Planned changes

1. Change the default report path from:
   - `.cache/sqlite_kb/reports/writer_publish_report.json`
   to:
   - `.cache/sqlite_kb/reports/writer_publish/<run_dir.name>/writer_publish_report.json`

2. Keep explicit `--output` support for callers that want a custom path.

3. Ensure the report always includes:
   - `run_dir`
   - `mode`
   - `repo_root`
   - `publish_root`
   - `worktree`
   - `branch`
   - `commit`
   - `push`
   - `failure_code`
   - artifact snapshot paths

#### Validation goals

- two publish runs do not overwrite each other’s reports
- the default report path is trivially discoverable from the run dir

### Workstream 2: Persist exported guideline snapshot outside the temp worktree

#### Objective

Guarantee that rendered guideline files survive beyond worktree cleanup.

#### Files to work in

- `scripts/retrieval/writer_host/publish.py`
- possibly small helper module for artifact copying
- tests covering exported snapshot persistence

#### Planned changes

1. Add a stable per-run export snapshot directory:
   - `.cache/sqlite_kb/reports/writer_publish/<run_dir.name>/exported_guidelines/`

2. After `run_export_rst(...)`, copy the generated output tree from:
   - `<worktree>/src/coding-guidelines/`
   into the stable snapshot directory.

3. Preserve:
   - generated guideline `.rst`
   - touched chapter `index.rst`
   - any generated/index files needed for reviewer inspection

4. Record snapshot paths and file counts in the publish report.

#### Validation goals

- after publish finishes, exported `.rst` files remain available even if the worktree is removed
- reviewer handoff no longer depends on the temp worktree

### Workstream 3: Preserve failure evidence by default

#### Objective

Stop deleting the only useful debugging context when publish fails.

#### Files to work in

- `scripts/retrieval/writer_host/publish.py`
- `scripts/retrieval/writer_host/publish_git.py`
- publish tests

#### Planned changes

1. Change cleanup policy so the worktree is removed only on fully successful publish, or when explicitly requested.

2. Recommended default behavior:
   - success with commit/push: cleanup allowed
   - conformance failure: preserve worktree
   - commit failure: preserve worktree
   - push failure: preserve worktree
   - unexpected exception: preserve worktree

3. Add explicit cleanup controls, recommended as:
   - `--keep-worktree`
   - optional `--cleanup-worktree-on-failure` only if truly needed

4. Record cleanup behavior in the publish report.

#### Validation goals

- failure paths preserve a recoverable worktree location
- successful paths still support cleanup if desired

## Phase 2: Make Publish Outcomes Explicit and Reviewable

### Workstream 4: Introduce explicit publish outcome states

#### Objective

Replace ambiguous pass/fail semantics with clearer outcome classification.

#### Files to work in

- `scripts/retrieval/writer_host/publish.py`
- tests for outcome classification

#### Planned changes

1. Represent outcomes distinctly:
   - `pass`
   - `dry_run`
   - `no_changes`
   - `fail`

2. Add structured failure codes such as:
   - `CONFORMANCE_FAILED`
   - `COMMIT_FAILED`
   - `PUSH_FAILED`
   - `EXPORT_FAILED`
   - `INGEST_FAILED`

3. Treat `no_changes` as a valid, inspectable result:
   - no commit
   - no push
   - exported snapshot still persisted
   - report explains why nothing was pushed

#### Validation goals

- reviewers can tell exactly why a branch was or was not pushed
- `no diff` stops being a confusing silent non-event

### Workstream 5: Build a publish review packet

#### Objective

Create one artifact that a reviewer can consume without reconstructing publish state manually.

#### Files to work in

- `scripts/retrieval/writer_host/packet.py` or new publish-specific packet helper
- `scripts/retrieval/services/writer_publish_service.py`
- tests for packet contents

#### Planned changes

1. Add a publish review packet zip under:
   - `.cache/sqlite_kb/reports/writer_publish/<run_dir.name>/writer_publish_review_packet.zip`

2. Include:
   - `writer_publish_report.json`
   - exported guideline snapshot
   - conformance report
   - `writer_publish.sqlite`
   - `annotation_policy_metrics.json`
   - FLS resolution reports
   - commit/push metadata
   - optionally the source run’s `writer_review_packet.zip` if present

3. Write a manifest next to the zip listing included artifacts.

#### Validation goals

- one durable packet is enough for reviewer handoff
- publish review no longer depends on live git branch state

## Phase 3: Lock in Failure-Path Behavior with Tests

### Workstream 6: Add publish durability and failure-path tests

#### Objective

Prevent regression into artifact-loss behavior.

#### Files to work in

- new/updated tests around publish service and writer host publish flow

#### Planned changes

Add tests for:

1. per-run default publish report path
2. conformance failure preserves exported snapshot and worktree metadata
3. push failure preserves exported snapshot and worktree metadata
4. no-diff / no-changes persists report and snapshot
5. successful publish writes packet and optionally cleans up worktree
6. dry-run behavior remains explicit and does not falsely claim exported output unless intentionally materialized

#### Validation goals

- the dangerous failure modes are covered directly
- artifact durability becomes a contract, not a best effort

## Phase 4: Validate Persistence on Small-Batch Publish Runs

### Workstream 7: Exercise durable publish artifacts on 1-target and 3-target batches

#### Objective

Prove that the publish durability changes hold under realistic small-batch runs before attempting broader publish usage.

#### Files and artifacts to validate

- per-run publish directories under `.cache/sqlite_kb/reports/writer_publish/`
- run-scoped `writer_publish_report.json`
- persisted `exported_guidelines/`
- publish review packet zip and manifest
- any preserved worktree path recorded in the publish report

#### Planned validation runs

1. Run a `1-target` publish scenario using a stable target set that is expected to complete the writer pipeline cleanly.

2. Run a `3-target` publish scenario using a mixed but still manageable target set so artifact persistence can be checked across multiple exported guideline files and chapter/index updates.

3. For each run, verify that all of the following persist after the publish command completes:
   - `.cache/sqlite_kb/reports/writer_publish/<run_dir.name>/writer_publish_report.json`
   - `.cache/sqlite_kb/reports/writer_publish/<run_dir.name>/exported_guidelines/`
   - `.cache/sqlite_kb/reports/writer_publish/<run_dir.name>/writer_publish_review_packet.zip`
   - `.cache/sqlite_kb/reports/writer_publish/<run_dir.name>/writer_publish_review_packet.manifest.json`

4. For each run, verify that report contents make outcome and persistence obvious:
   - `status`
   - `failure_code`
   - `branch`
   - `worktree`
   - `commit`
   - `push`
   - exported snapshot paths and counts

5. If cleanup occurs on a successful path, confirm that persisted artifacts remain available after worktree removal.

6. If the run takes a failure or no-changes path, confirm that failure evidence remains inspectable and that the report clearly explains why push did not happen.

#### Validation goals

- `1-target` and `3-target` publish runs both leave behind durable reviewer-facing artifacts
- artifact persistence is proven on realistic small-batch runs, not just mocked tests
- successful cleanup no longer destroys reviewer handoff material
- failure or no-changes outcomes remain auditable after the command exits

## Recommended Implementation Order

1. Run-scoped report path
2. exported guideline snapshot persistence
3. failure-preserving cleanup policy
4. explicit outcome states
5. publish review packet
6. failure-mode tests
7. 1-target and 3-target publish persistence validation

## Acceptance Criteria

This plan is complete when all of the following are true:

- running `writer-publish` creates a run-scoped report under the per-run publish directory
- every non-dry-run publish attempt leaves behind a durable exported guideline snapshot
- conformance failure no longer destroys the rendered files needed for debugging
- push failure no longer destroys the rendered files needed for debugging
- `no_changes` is reported explicitly and still leaves behind durable artifacts
- a publish review packet exists and can be handed to a reviewer directly
- tests cover success, conformance failure, push failure, and no-changes behavior
- small-batch `1-target` and `3-target` publish runs prove that durable artifacts persist after command completion

## Recommended Follow-On Command Validation

After implementation, validate with:

1. a publishable run that passes and pushes
2. a forced conformance-failure scenario
3. a simulated push-failure scenario
4. a no-diff scenario
5. reviewer-packet generation for each scenario
6. a `1-target` publish persistence run
7. a `3-target` publish persistence run
