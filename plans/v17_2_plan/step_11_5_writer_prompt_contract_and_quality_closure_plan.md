# Step 11.5 Writer Prompt/Contract and Quality Closure Plan

## Purpose

This plan closes the remaining non-transport failures observed after the Step 11.5 writer execution recovery work. The broad writer pipeline is now operational: the canonical `writer-targets` -> `writer-evidence --corpora` -> `writer-run --evidence-manifest` flow completes successfully for 1-target, 3-target, and 23-target runs. The remaining problems are no longer infrastructure failures. They are prompt-contract, schema-convention, and quality-policy gaps.

The goal of this plan is to eliminate the residual 23-target violations and convert the writer pipeline from "operational but permissive" to "operational and contract-stable".

This plan is intentionally split into two execution phases:

1. Prompt/contract closure for correctness-critical residual failures.
2. Evidence-synth quality gate hardening for semantically weak but currently passing outputs.

The first phase should be treated as required for writer correctness. The second phase should be treated as required for preventing silent quality regressions.

## Scope

This plan covers:

- evidence-synth prompt ID bleed-through caused by canonical example content
- example-author skip-justification field-name mismatch
- cross-role citation-key convention drift between author roles, metadata, merge validation, and publish/render consumers
- quality-gate hardening for evidence-synth outputs that are structurally valid but semantically empty or example-contaminated
- test and regression updates needed to lock the corrected behavior in place

This plan does not cover:

- corpus-selection flow changes already addressed by prior Step 11.5 work
- writer/judge transport unification work unless a discovered code path directly blocks this plan
- semantic backend environment failures outside the writer prompt/contract path

## Problem Statement

The 23-target recovery run narrowed the residual failures into three distinct buckets.

### A. Example-author field-name mismatch

Targets `RET-RESOLVE-007` and `RET-RESOLVE-008` failed role validation because the model emitted:

- `non_compliant_miri_justification`
- `compliant_miri_justification`

but validation and downstream publish logic require:

- `non_compliant_miri_skip_justification`
- `compliant_miri_skip_justification`

This is a contract naming mismatch, not a transport or runtime failure.

### B. Citation-key convention drift

The metadata role currently allows both of these incompatible `citation_key_map` shapes to appear:

- `citation_key -> evidence_id`
- `evidence_id -> citation_key`

Cross-role merge validation tolerates some fallback cases, but the mixed conventions cause inconsistent behavior. In the residual run, `RET-NEG-001` failed merge validation because authors emitted raw evidence IDs while metadata used a map orientation inconsistent with validator expectations.

This is a multi-role contract-definition problem.

### C. Evidence-synth example bleed-through and semantic weakness

Several evidence-synth outputs passed current validation despite using `EXAMPLE-001` as `prompt_id` or emitting blank semantic fields:

- prompt-id bleed examples: `RET-ISSUE-002`, `RET-ISSUE-004`, `RET-RESOLVE-009`, `RET-NEG-001`
- semantically weak negative outputs: `RET-NEG-001`, `RET-NEG-002`

This is currently possible because:

- the evidence-synth prompt contract still teaches `EXAMPLE-001` in its canonical JSON example
- runtime normalization only fills missing `prompt_id`; it does not override a wrong-but-non-empty prompt ID
- evidence-synth gate checks are permissive and do not reject blank `hazard`, `mechanism`, `mitigation`, or empty `construct_scope`

## Key Evidence

### Canonical run artifacts

- `.cache/sqlite_kb/reports/recovery_23_target_run/role_validation_report.json`
- `.cache/sqlite_kb/reports/recovery_23_target_run/evidence_synthesizer_gate_report.json`
- `.cache/sqlite_kb/reports/recovery_23_target_run/writer_host_run_summary.json`
- `.cache/sqlite_kb/reports/recovery_23_target_run/drafts.jsonl`
- `.cache/sqlite_kb/reports/recovery_23_target_run/writer_subagent_outputs/example_author.jsonl`
- `.cache/sqlite_kb/reports/recovery_23_target_run/writer_subagent_outputs/evidence_synthesizer.jsonl`
- `.cache/sqlite_kb/reports/recovery_23_target_run/writer_subagent_outputs/metadata_citation_curator.jsonl`

### Contract and validator files implicated

- `config/s0/writer_prompt_contracts.yaml`
- `scripts/retrieval/writer_host/validation.py`
- `scripts/retrieval/writer_host/runtime.py`
- `scripts/retrieval/writer_host/roles.py`
- `scripts/retrieval/writer_host/publish.py`
- `scripts/retrieval/writer_host/publish_ingest.py`

### Existing tests that already touch this area

- `tests/unit/sqlite_kb/test_writer_example_annotation_policy.py`
- `tests/unit/sqlite_kb/test_writer_host_runtime.py`
- `tests/unit/sqlite_kb/test_writer_evidence.py`
- `tests/unit/sqlite_kb/test_writer_roles.py`

Additional tests will likely need to be added rather than only modifying existing ones.

## Desired End State

After this plan is complete:

- evidence-synth outputs always use the actual target prompt ID, never the prompt example ID
- example-author outputs use the exact skip-justification field names expected by validation and publish paths
- metadata, author roles, merge validation, and publish/render consumers all share one unambiguous `citation_key_map` convention
- evidence-synth gate fails outputs that are obviously example-contaminated or semantically empty
- the 23-target canonical recovery regression passes without residual writer-role violations from these buckets

## Execution Strategy

## Phase 1: Prompt/Contract Closure

### Workstream 1: Fix evidence-synth prompt example contamination

#### Objective

Remove prompt-contract teaching that causes the model to copy `EXAMPLE-001` into live evidence-synth outputs.

#### Files to work in

- `config/s0/writer_prompt_contracts.yaml`
- `scripts/retrieval/writer_host/runtime.py`
- `tests/unit/sqlite_kb/test_writer_evidence.py`
- `tests/unit/sqlite_kb/test_writer_host_runtime.py`
- potentially `tests/unit/sqlite_kb/test_writer_roles.py`

#### Planned changes

1. Update the canonical evidence-synth JSON example in `config/s0/writer_prompt_contracts.yaml` so that:
   - `prompt_id` is clearly target-derived rather than fixed to `EXAMPLE-001`
   - example `claim_id` values explicitly show the live prompt ID pattern without encouraging a reusable literal
   - wording clearly states that the output `prompt_id` must match the live target/prompt context provided in the prompt

2. Tighten runtime normalization in `scripts/retrieval/writer_host/runtime.py` so that it can detect and repair obvious prompt-example contamination instead of only filling missing values.

3. Decide and implement the exact normalization rule. Recommended rule:
   - if `prompt_id` is missing, set it to `target_id`
   - if `prompt_id` is present but does not equal the live target prompt ID, rewrite it to the live target prompt ID and rewrite claim IDs accordingly

4. Preserve observability by recording that normalization corrected a contaminated prompt ID, not just a missing one.

#### Validation goals

- no evidence-synth output should retain `EXAMPLE-001` for live targets
- no `claim_id` should remain prefixed by `EXAMPLE-001::claim::...`
- tests should explicitly prove that wrong prompt IDs are normalized, not just missing prompt IDs

### Workstream 2: Close example-author skip-justification naming mismatch

#### Objective

Align prompt contract, validation, and downstream publish logic on one exact field name set for Miri skip justifications.

#### Files to work in

- `config/s0/writer_prompt_contracts.yaml`
- `scripts/retrieval/writer_host/validation.py`
- `scripts/retrieval/writer_host/publish.py`
- `scripts/retrieval/writer_host/publish_ingest.py`
- `tests/unit/sqlite_kb/test_writer_example_annotation_policy.py`
- potentially `tests/unit/sqlite_kb/test_writer_host_runtime.py`

#### Planned changes

1. Make the example-author prompt text in `config/s0/writer_prompt_contracts.yaml` explicitly name:
   - `non_compliant_miri_skip_justification`
   - `compliant_miri_skip_justification`

2. Ensure the schema example and prompt wording do not use shortened alternatives such as `*_miri_justification`.

3. Review whether validation should accept legacy aliases for compatibility. Recommended decision:
   - do not preserve alias acceptance in final contract behavior
   - if temporary compatibility is needed during rollout, normalize aliases centrally and log that normalization occurred
   - keep persisted/public artifact schema on the canonical `*_skip_justification` names only

4. Add tests proving that:
   - `skip` without `*_skip_justification` fails
   - `skip` with the exact canonical field name passes
   - alias-only payloads are either normalized intentionally or rejected intentionally, depending on final implementation choice

#### Validation goals

- `RET-RESOLVE-007` and `RET-RESOLVE-008` style failures disappear
- the contract becomes self-describing enough that the model stops emitting the shorter alias

### Workstream 3: Unify cross-role citation-key convention

#### Objective

Define and enforce one citation-key convention across metadata generation, author role outputs, merge validation, publish, and rendering.

#### Files to work in

- `config/s0/writer_prompt_contracts.yaml`
- `scripts/retrieval/writer_host/validation.py`
- `scripts/retrieval/writer_host/roles.py`
- `scripts/retrieval/writer_host/publish.py`
- `scripts/retrieval/writer_host/publish_ingest.py`
- `scripts/retrieval/rendering/rst_renderer.py`
- `scripts/retrieval/rendering/rerender_from_artifacts.py`
- `tests/unit/sqlite_kb/test_writer_host_runtime.py`
- `tests/unit/test_rendering_v2.py`
- new writer-host unit tests if necessary

#### Required contract decision

This workstream must choose one authoritative orientation for `citation_key_map`.

Recommended convention:

- `citation_key_map` should be `citation_key -> evidence_id`

Reasoning:

- the map name implies lookup from emitted citation key to grounding evidence
- author outputs naturally emit citation keys, not evidence IDs, once metadata has curated them
- publish/render stages are easier to reason about when citation keys resolve directly to evidence
- validator logic already reads more naturally with this orientation

#### Planned changes

1. Update prompt contract text for `metadata_citation_curator` to state the required map orientation explicitly.

2. Update validation logic in `scripts/retrieval/writer_host/validation.py` so it no longer quietly tolerates mixed semantics.

3. Review all producer and consumer code paths and convert them to the same direction:
   - metadata role output handling
   - merge validation
   - publish artifact emission
   - rendering and rerendering consumers

4. Decide how author roles should behave before metadata exists. Recommended contract:
   - author roles may emit raw evidence IDs during generation if metadata has not yet assigned curated citation keys
   - merge validation should convert or validate against the canonical metadata map in a single explicit way rather than relying on orientation ambiguity

5. Add or update tests proving that:
   - metadata map orientation is canonical and stable
   - author citation lists can be resolved deterministically
   - reversed maps are rejected or normalized in one explicit place only
   - `RET-NEG-001` style cross-role failures no longer occur

#### Validation goals

- no mixed `citation_key_map` orientation appears in writer outputs
- merge validation either succeeds deterministically or fails with precise contract errors
- downstream render/publish artifacts still resolve citations correctly

## Phase 2: Evidence-Synth Quality Gate Hardening

### Workstream 4: Reject example bleed-through and semantically empty evidence synth outputs

#### Objective

Upgrade evidence-synth validation from minimal schema compliance to minimum semantic adequacy.

#### Files to work in

- `scripts/retrieval/writer_host/validation.py`
- `scripts/retrieval/writer_host/runtime.py`
- `scripts/retrieval/writer_host/artifacts.py`
- `tests/unit/sqlite_kb/test_writer_host_runtime.py`
- `tests/unit/sqlite_kb/test_writer_evidence.py`
- `tests/unit/sqlite_kb/test_writer_quality_gate.py`
- new dedicated evidence-synth gate tests if needed

#### Planned changes

1. Add explicit evidence-synth validation for prompt ID correctness:
   - fail or normalize when `prompt_id != live target prompt id`

2. Add minimum semantic-content checks for evidence synth outputs:
   - `hazard` non-empty
   - `mechanism` non-empty
   - `mitigation` non-empty
   - `construct_scope` non-empty when evidence is non-empty and the target is not an explicit abstain case

3. Decide whether blank semantic fields should be normalized or rejected. Recommended rule:
   - reject, do not auto-fill
   - semantic content is authored content, not safe normalization material

4. Add explicit reporting so failures appear in gate and run-summary artifacts with category names that distinguish:
   - prompt example contamination
   - semantic field omission
   - empty construct scope

5. Confirm whether negative-query targets need a documented abstain shape. If so, define it explicitly rather than letting empty semantic fields masquerade as valid outputs.

#### Validation goals

- `RET-NEG-001` and `RET-NEG-002` style empty evidence-synth outputs no longer pass silently
- future prompt example contamination is caught immediately in unit tests and run artifacts

## Test Plan

### Unit tests to add or strengthen

#### Prompt-contract and normalization tests

- wrong `prompt_id` is rewritten to live target prompt ID
- claim IDs are rewritten to match corrected prompt ID
- missing `prompt_id` still normalizes correctly
- prompt example in contract does not encourage literal `EXAMPLE-001` reuse in tests

Likely files:

- `tests/unit/sqlite_kb/test_writer_host_runtime.py`
- `tests/unit/sqlite_kb/test_writer_evidence.py`

#### Example-author annotation policy tests

- canonical skip-justification field names pass validation
- missing canonical skip-justification fields fail validation
- alias-only names are handled exactly as intended by final implementation

Likely files:

- `tests/unit/sqlite_kb/test_writer_example_annotation_policy.py`

#### Citation convention tests

- canonical `citation_key_map` orientation is enforced
- merge validation resolves author citations against metadata consistently
- reversed or ambiguous maps fail with explicit errors unless intentionally normalized in one central place

Likely files:

- `tests/unit/sqlite_kb/test_writer_host_runtime.py`
- `tests/unit/test_rendering_v2.py`
- new targeted tests under `tests/unit/sqlite_kb/`

#### Quality-gate tests

- evidence synth with `EXAMPLE-001` prompt ID fails or is normalized as designed
- blank `hazard`/`mechanism`/`mitigation` fails gate
- empty `construct_scope` fails gate where appropriate
- explicit abstain shape passes only when correctly formed

Likely files:

- `tests/unit/sqlite_kb/test_writer_quality_gate.py`
- new targeted writer evidence gate tests if existing coverage is too indirect

### Regression runs

After unit tests pass, rerun canonical writer flows using a deliberate regression ladder rather than arbitrary sample targets:

1. 1-target canonical regression using a previously failing residual target
2. 3-target canonical regression using a mixed mini-regression that spans the residual buckets
3. full 23-target canonical regression

#### 1-target regression requirements

The 1-target run must not be a known-clean happy-path sample. It should be used as a fast gate on a target that previously exposed one of the residual failures this plan is intended to fix.

Recommended first-choice targets:

- prompt-id bleed / evidence-synth contamination: `RET-ISSUE-002` or `RET-RESOLVE-009`
- example-author skip-justification mismatch: `RET-RESOLVE-007` or `RET-RESOLVE-008`
- citation-sensitive negative-path case: `RET-NEG-001`

Recommended execution rule:

- choose the 1-target regression based on the workstream just completed
- if the most recent code changes touched evidence-synth prompt normalization or gate behavior, prefer `RET-ISSUE-002`, `RET-RESOLVE-009`, or `RET-NEG-001`
- if the most recent code changes touched example-author schema or annotation validation, prefer `RET-RESOLVE-007` or `RET-RESOLVE-008`

#### 3-target regression requirements

The 3-target run must be a purposeful mixed mini-regression and should include one representative target from each residual failure bucket so the run exercises cross-role interactions rather than isolated single-target behavior.

Required bucket coverage:

- one prompt-id bleed / evidence-synth contamination target
- one example-author skip-justification mismatch target
- one citation-convention or negative-path target

Recommended 3-target composition:

1. `RET-ISSUE-002` or `RET-RESOLVE-009` for prompt-id bleed
2. `RET-RESOLVE-007` or `RET-RESOLVE-008` for example-author skip-justification naming
3. `RET-NEG-001` for citation-map and negative-path coverage

Recommended default set:

1. `RET-ISSUE-002`
2. `RET-RESOLVE-007`
3. `RET-NEG-001`

This default set gives broad signal because it exercises:

- evidence-synth prompt contamination and claim-ID rewriting
- example-author field-name compliance for Miri skip justifications
- metadata/author/merge citation resolution on the negative-path case that previously exposed convention drift

#### Phase-gated use of the regression ladder

The regression ladder should be used as phase gates, not just as one final verification pass.

Recommended gating sequence:

1. After the prompt-contract fixes and associated unit tests land, run the targeted 1-target regression.
2. After prompt normalization and example-author contract fixes are stable, run the mixed 3-target regression.
3. After citation-convention unification and quality-gate tightening are complete, rerun the 1-target and 3-target gates if needed, then run the full 23-target regression.

The purpose of this sequence is to ensure:

- small regressions fail fast close to the code that introduced them
- cross-role contract interactions are exercised before paying for the broad run
- the 23-target run is used as final confirmation rather than as the first place new breakage is discovered

Required artifacts to inspect after the 23-target run:

- `writer_host_run_summary.json`
- `role_validation_report.json`
- `evidence_synthesizer_gate_report.json`
- `writer_subagent_outputs/evidence_synthesizer.jsonl`
- `writer_subagent_outputs/example_author.jsonl`
- `writer_subagent_outputs/metadata_citation_curator.jsonl`
- `drafts.jsonl`

Success criterion: no residual failures from the three buckets addressed by this plan.

## Implementation Order

Recommended sequence:

1. fix evidence-synth prompt contract example
2. harden runtime prompt-id normalization
3. fix example-author skip-justification contract naming
4. lock those behaviors with unit tests
5. run the targeted 1-target regression on a previously failing residual case
6. run the mixed 3-target regression spanning the residual buckets
7. unify `citation_key_map` orientation across producer/consumer code
8. add citation convention tests
9. harden evidence-synth semantic gate checks
10. add quality-gate tests
11. rerun the targeted 1-target and mixed 3-target regressions if the later changes touched shared contracts or validation
12. run the full 23-target canonical regression

This order reduces churn because:

- prompt contamination should be removed before evaluating whether citation and semantic checks still fail
- the targeted 1-target and mixed 3-target regressions provide early signal on the exact residual buckets this plan addresses
- citation convention changes can affect downstream rendering/publish consumers and should be stabilized before the final broad regression
- quality-gate tightening should happen after prompt/contract correctness is restored, so new failures are meaningful rather than mixed with already-known contract bugs
- the 23-target run should remain the final confirmation step, not the first regression surface used to discover obvious contract mistakes

## Risks and Mitigations

### Risk 1: Over-normalization hides model failures

If runtime silently rewrites too many fields, bad prompts may appear fixed while the model continues to produce contract-invalid outputs.

Mitigation:

- normalize only deterministic identity fields such as prompt ID and claim ID prefixes
- do not auto-fill authored semantic fields such as `hazard`, `mechanism`, or `mitigation`
- preserve explicit reporting when normalization corrected a contamination pattern

### Risk 2: Citation-key convention changes break rendering or publish artifacts

Mitigation:

- inspect all consumers before finalizing the chosen orientation
- add rendering/publish-facing tests, not just writer-host tests
- avoid mixed compatibility logic spread across multiple modules

### Risk 3: Negative targets may need an abstain contract rather than stricter non-empty semantics

Mitigation:

- decide explicitly whether negative-query evidence synth outputs are expected to be full synths or structured abstains
- encode that decision in validation and tests instead of leaving emptiness as an accidental pass condition

## Deliverables

This plan should produce:

- corrected prompt contracts in `config/s0/writer_prompt_contracts.yaml`
- corrected writer-host validation/normalization behavior in `scripts/retrieval/writer_host/`
- unified citation-key convention across writer, publish, and rendering paths
- expanded unit test coverage for prompt IDs, skip justifications, citation maps, and semantic gate checks
- passing canonical 1-target, 3-target, and 23-target writer regressions for the failure buckets covered here

## Completion Criteria

This plan is complete when all of the following are true:

- no evidence-synth outputs in the canonical regression use `EXAMPLE-001` as live `prompt_id`
- no claim IDs in canonical regression outputs retain `EXAMPLE-001::claim::...`
- example-author outputs use the exact `*_miri_skip_justification` field names required by validation/publish paths
- `citation_key_map` orientation is singular, documented, enforced, and covered by tests
- `RET-NEG-001` style cross-role citation failures no longer appear
- blank evidence-synth semantic fields no longer pass silently unless they match an explicit abstain contract
- the targeted 1-target regression passes on a previously failing residual case
- the mixed 3-target regression passes with one representative from each residual bucket
- the full 23-target canonical regression completes without residual failures from these categories

## Files Expected To Be Worked In

Primary files:

- `config/s0/writer_prompt_contracts.yaml`
- `scripts/retrieval/writer_host/runtime.py`
- `scripts/retrieval/writer_host/validation.py`
- `scripts/retrieval/writer_host/roles.py`
- `scripts/retrieval/writer_host/publish.py`
- `scripts/retrieval/writer_host/publish_ingest.py`
- `scripts/retrieval/rendering/rst_renderer.py`
- `scripts/retrieval/rendering/rerender_from_artifacts.py`

Primary tests:

- `tests/unit/sqlite_kb/test_writer_example_annotation_policy.py`
- `tests/unit/sqlite_kb/test_writer_host_runtime.py`
- `tests/unit/sqlite_kb/test_writer_evidence.py`
- `tests/unit/sqlite_kb/test_writer_quality_gate.py`
- `tests/unit/test_rendering_v2.py`

Artifacts used for verification only:

- `.cache/sqlite_kb/reports/recovery_23_target_run/role_validation_report.json`
- `.cache/sqlite_kb/reports/recovery_23_target_run/evidence_synthesizer_gate_report.json`
- `.cache/sqlite_kb/reports/recovery_23_target_run/drafts.jsonl`
- `.cache/sqlite_kb/reports/recovery_23_target_run/writer_subagent_outputs/example_author.jsonl`
- `.cache/sqlite_kb/reports/recovery_23_target_run/writer_subagent_outputs/evidence_synthesizer.jsonl`
- `.cache/sqlite_kb/reports/recovery_23_target_run/writer_subagent_outputs/metadata_citation_curator.jsonl`
