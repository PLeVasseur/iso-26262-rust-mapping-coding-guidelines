# Phase A Recovery Plan (opencode v1)

Date: 2026-02-27
Scope: ISO 26262 Table 1 S0 Phase A
Objective: recover to real LLM-authored, judge-evaluated output with strict gate enforcement and reproducible OpenCode execution.

## 1) Why this revision exists

This plan supersedes ad-hoc execution and folds in the critical fixes from the prior LLM-first plan while hardening operator controls.

Primary failure classes observed:

- semantic content in publishable path was template-assembled,
- Stage B judge outputs used stubbed/fixed-score logic,
- required gates were not all enforced as hard release criteria,
- execution sequencing drifted from startup prerequisites,
- retries were not governed by deterministic stop/escalation rules.

## 1.1) Crosswalk to prior LLM-first plan

This revision explicitly carries forward and hardens the prior plan:

- Prior 3.1 -> current 8.1 (evidence synthesizer prompt + exit gate)
- Prior 3.2 -> current 8.2 (downstream role chain checks)
- Prior 3.3 -> current 8.3 (template path removal)
- Prior 3.4 -> current 8.4 (invocation proof contract)
- Prior 4.1 -> current 9.1 (real Stage B judge set)
- Prior 4.2 -> current 9.2 (aggregation policy with bootstrap exception)
- Prior 5.1 -> current 10.1 (duplicate similarity gate)
- Prior 5.2 -> current 10.2 (construct-evidence alignment + synonyms)
- Prior 5.3 -> current 10.3 (example execution semantics)
- Prior 5.4 -> current 10.4 (modality/category consistency)
- Prior 6/6.1 -> current 5 and 11 (startup checklist + mode promotion)
- Prior 7.1 -> current 13 (outcome-based escalation options)

## 2) Non-negotiables

1. No hardcoded target-specific semantic prose in publishable path.
2. No fixed-score Stage B judge outputs.
3. Renderer owns structure only, never semantic synthesis.
4. No reviewer packet unless enforcement status is `pass`.
5. Bootstrap-first for initial recovery runs.
6. Real LLM role calls only for writer chain and Stage B judges.
7. Required critical gates must pass: duplicate, alignment, example semantics, modality/category.
8. Escalation is outcome-based (gate/budget driven), not day-based.

## 3) Single source of truth paths

- Recovery plan: `plans/phase-a-recovery-plan-opencode-v1.md`
- Baseline plan reference: `/Users/pete.levasseur/opencode-project-agents/iso-26262-rust-mapping-coding-guidelines/plans/phase-a-recovery-plan-llm-first-v1.md`
- Writer contracts: `config/s0/writer_prompt_contracts.yaml`
- Judge contracts: `config/s0/judge_prompt_contracts.yaml`
- Prompt examples: `config/s0/drafting_prompt_contract.yaml`
- Gate policy: `config/s0/s0_gate_policy.yaml`
- Targets: `config/s0/s0_targets.yaml`
- Phase A service: `scripts/retrieval/services/s0_phase_a_service.py`
- CLI entrypoint: `scripts/sqlite_kb.py`
- Reports root: `.cache/sqlite_kb/reports/<run_id>/`
- Enforcement report: `.cache/sqlite_kb/reports/<run_id>/calibration_quality_enforcement_report.json`

## 4) OpenCode operating model (required)

OpenCode must be the execution framework.

### 4.1 Agents and roles

- `Plan` primary agent: mapping, analysis, acceptance criteria checks.
- `Build` primary agent: implementation and command execution.
- `@explore` subagent: read-only discovery, file/function mapping.
- `@general` subagent: bounded implementation/research subtasks.

### 4.2 Mandatory subagent invocation contracts

Each `@explore` run must return:

- section ID(s) being mapped,
- files/functions touched,
- current-state gap vs plan requirement,
- expected artifacts and verification command.

Each `@general` run must return:

- exact edit intent bounded to one section,
- files edited,
- command(s) run and result summary,
- produced artifact paths,
- gate impact (`pass`/`fail`/`not-run`).

### 4.3 Enforced sequence in OpenCode

1. Start in `Plan`.
2. Use `@explore` to map one section.
3. Switch to `Build`.
4. Use `@general` for one section only.
5. Validate and report.
6. Return to `Plan` for next section mapping.

No multi-section edit batches.

## 5) Startup checklist (section 6.1 hard gate)

Before any major action, all six checks must pass:

Definition: a major action is any code edit, any pipeline command after startup checks, or any rollout/promotion step.

1. Mode explicitly `bootstrap` for first recovery run.
2. Active target set is the five-target calibration set.
3. Publishable path has no active template semantic branch.
4. Writer prompt contracts are loaded and non-placeholder (not one-line stubs).
5. Stage B path points to real judge execution (no fixed-score stubs).
6. Active run artifact root is writable and clean for that run ID.

Required artifact:

- `.cache/sqlite_kb/reports/<run_id>/startup_checklist_report.json`

Any startup checklist fail is immediate stop.

## 6) Checkpoint-first safety

Before rollback or implementation, create a checkpoint commit.

Checkpoint scope policy: commit all current changes to avoid accidental data loss during rollback.

Commands:

```bash
git status --short
git add -A
git commit -m "chore: checkpoint pre-opencode-phase-a-recovery"
git rev-parse --short HEAD
```

Required run note fields:

- checkpoint commit hash,
- timestamp,
- operator/agent name.

## 7) Phase 1 - Repair / rollback

Objective: undo assistant-caused regressions without losing user work.

### 7.1 Candidate rollback set

- `scripts/retrieval/services/s0_phase_a_service.py`
- `scripts/sqlite_kb.py`
- `config/s0/writer_prompt_contracts.yaml`
- `config/s0/judge_prompt_contracts.yaml`
- `data/sqlite_kb_manifest.yaml`
- `config/s0/construct_synonyms.yaml` (if assistant-created)
- `config/s0/target_execution_modes.yaml` (if assistant-created)

### 7.2 Rollback decision tree

1. Prefer OpenCode `/undo` in the original session.
2. If unavailable, do targeted restore/delete on rollback set only.
3. Never use destructive repo-wide reset.
4. Preserve any unrelated user preexisting modifications.

### 7.3 Phase 1 acceptance

- Working tree is clean or contains only user-acknowledged preexisting edits.
- Syntax check passes for touched entry points.
- No unintended assistant-created files remain.

## 8) Phase 2 - Real output chain (Days 1-5)

### 8.1 Evidence synthesizer (critical path)

Prompt must include all:

- worked positive/negative examples from drafting contract,
- forbidden patterns from writer contract,
- required output schema,
- style excerpts from style guide text,
- explicit length/structure bounds.

Exit gate (must pass before downstream rollout):

- schema-valid on >=3/5 targets,
- >=1 construct-specific normative claim with evidence binding on >=3/5,
- banned-pattern check pass on >=3/5.

### 8.2 Downstream writer roles in chain order

1. `amplification_author`
2. `example_author`
3. `rationale_author`
4. `metadata_citation_curator`

Role readiness checks:

- Amplification: construct-specific terms, no generic boilerplate.
- Example: both examples present; code aligns to normative construct.
- Rationale: explicit hazard -> mechanism -> consequence causal chain.
- Metadata/citation: auditable citation entries; `shall`/mandatory alignment.

### 8.3 Remove template semantic path

After chain works on >=3 targets:

- remove prompt-id/row-id semantic branching,
- remove hardcoded semantic prose blocks,
- retain renderer structure assembly only.

Provenance rule: every semantic field must trace to writer role output artifact.

### 8.4 Invocation proof contract

Each writer role invocation record must include non-empty:

- `system_request_id`
- `request_started_at`
- `response_received_at`
- `prompt_digest`
- `response_digest`
- `transport_status`
- `provider_model`

Pass criteria for a real invocation proof:

- `transport_status` must be `ok`,
- `response_received_at` must be greater than or equal to `request_started_at`,
- all listed fields must be present and non-empty.

Model/decode policy:

- writer temperature: `0.2`
- judge temperature: `0.0`
- model pinned per run and recorded.

## 9) Phase 3 - Real Stage B judgments (Days 5-8)

### 9.1 Stage B judge set

Hard judges:

- `evidence_auditor`
- `golden_shape_comparator`
- `writer_output_auditor`

Soft judges:

- `functional_safety_relevance`
- `usability_actionability`
- `exemplar_usage_auditor`

Diagnostic only:

- `holistic_pairwise`

No stub or fixed scores permitted.

### 9.2 Target aggregation policy

- Publishable: any hard fail or hard abstain -> `blocked`.
- Bootstrap: hard fail -> `blocked`, hard abstain -> `review`.
- Candidate condition: soft pass >=2, soft fail = 0, soft abstain <=1.
- Otherwise -> `review`.

## 10) Phase 4 - Critical gates (Days 8-10)

### 10.1 Duplicate similarity gate

- Inputs: normalized normative body, rationale, example narratives, example code.
- Metric: pairwise 4-gram Jaccard.
- Threshold: `> 0.60`.
- Bootstrap: same construct-family pair -> `review`.
- Publishable: violation -> `block`.

### 10.2 Construct-evidence alignment gate

Rule per normative claim:

- at least one evidence ref contains >=1 term from claim target `construct_scope`,
- synonyms expanded via `config/s0/construct_synonyms.yaml`.

### 10.3 Example execution semantics gate

Per target expected mode in `config/s0/target_execution_modes.yaml`:

- `runnable`, `no_run`, `should_panic`, or `compile_fail`.

Hard rule:

- runtime-hazard targets must not use `:compile_fail:`.

### 10.4 Modality/category consistency gate

- `shall` -> `mandatory`
- `should` -> `advisory`

Mismatch is blocking.

### 10.5 Retrieval precheck

Keep thresholds from prior plan:

- >=3 excerpts,
- >=2 excerpts with construct indicators,
- >=2 distinct sources,
- >=220 characters total evidence payload.

## 11) Mode policy and promotion

Bootstrap first.

Promotion to publishable requires two consecutive bootstrap passes with:

- invocation proof pass on all non-abstain targets,
- no hard-gate flaps across the two runs,
- duplicate gate dry-run in publishable policy mode passes.

Publishable pass requires:

- `abstain_rate <= 0.40`
- `candidate_targets >= 3`
- no `review` targets.

### 11.1 Artifact-of-record mapping for promotion checks

- `abstain_rate`: `judge_aggregate.json` field `abstain_rate`
- `candidate_targets`: `judge_aggregate.json` field `candidate_grade_count`
- `review targets`: `judge_aggregate.json` field `review_count` must be `0`
- enforcement status: `calibration_quality_enforcement_report.json` field `status` must be `pass`

## 12) Run budgets and timeouts

- `max_writer_calls_per_run = 120`
- `max_stage_b_judge_calls_per_run = 80`
- `max_substantive_retries_per_target = 6`
- writer timeout `90s`
- judge timeout `60s`

Exceeding budget is hard stop plus escalation note.

## 13) Retry and escalation decision table

- Startup checklist fail: fix checklist item and rerun startup only.
- Hard gate fail in bootstrap: one bounded fix iteration, then rerun.
- Same hard gate fails twice: escalate, stop downstream.
- Evidence synthesizer gate repeatedly misses exit criteria: escalate before downstream expansion.
- Missing secret/config for real calls: stop until dependency supplied.

Definition: repeated gate miss means two consecutive failures of the same gate for the same phase and target set.

### 13.1 Escalation options (when triggered by outcome)

When escalation is triggered by repeated gate misses or retry-budget exhaustion:

1. Stop downstream rollout.
2. Record top 3 failure patterns from artifacts.
3. Choose one path before proceeding:
   1. Prompt redesign with stronger examples and tighter forbidden patterns.
   2. Model/decode adjustment for writer roles.
   3. Temporary target-scope reduction for prompt hardening.

## 14) Artifact contract (must exist)

Per run ID, minimum files:

- `startup_checklist_report.json`
- `writer_subagent_outputs/style_context_bundle.json`
- `writer_subagent_outputs/prompt_contract_snapshot.json`
- `writer_subagent_outputs/subagent_invocation_trace.json`
- `writer_subagent_outputs/evidence_synthesizer.jsonl`
- `writer_subagent_outputs/amplification_author.jsonl`
- `writer_subagent_outputs/example_author.jsonl`
- `writer_subagent_outputs/rationale_author.jsonl`
- `writer_subagent_outputs/metadata_citation_curator.jsonl`
- `stage_b_judges/<judge>/<target>.json` (for required judges)
- `judge_aggregate.json`
- `duplicate_similarity_gate_report.json`
- `construct_evidence_alignment_report.json`
- `example_execution_semantics_report.json`
- `modality_category_consistency_report.json`
- `calibration_quality_enforcement_report.json`

## 15) Execution sequence (fresh run id each attempt)

Run-ID policy:

- Use a new run ID for every attempt.
- Never reuse run IDs.
- If reuse is detected, startup checklist must fail and execution stops.

```bash
uv run python scripts/sqlite_kb.py doctor --run-id <RUN_ID> --mode bootstrap
uv run python scripts/sqlite_kb.py enumerate-targets --run-id <RUN_ID> --mode bootstrap
uv run python scripts/sqlite_kb.py calibration-run --run-id <RUN_ID> --mode bootstrap
uv run python scripts/sqlite_kb.py enforce-calibration-quality --run-id <RUN_ID> --mode bootstrap
```

Reviewer packet command (allowed only when enforcement is pass):

Reviewer packet gate policy:

- Allowed only if `.cache/sqlite_kb/reports/<RUN_ID>/calibration_quality_enforcement_report.json` has `status: pass` for the same run ID.

```bash
uv run python scripts/sqlite_kb.py pack-reviewer-packet --run-id <RUN_ID>
```

## 16) CI regression guards (post-fix lock-in)

This section is deferred and is not required for v1 recovery acceptance.

Add denylist/static checks preventing reintroduction:

- template semantic phrase injections in publishable path,
- prompt-id/row-id semantic branching for content,
- fixed Stage B score constants/stub loops,
- semantic field emission without role provenance.

## 17) Reporting format (mandatory every update)

- Current phase
- What was executed
- Artifacts produced (full paths)
- Gate status
- Blockers and next step

## 18) Stop conditions

Immediate stop on any of:

- startup checklist failure,
- missing runtime secret for real LLM calls,
- hard gate fail without remaining retry budget,
- repeated evidence synthesizer gate miss requiring escalation,
- enforcement status != `pass` when attempting reviewer packet.

## 19) Acceptance and definition of done

Recovery is successful only when all are true:

1. Non-abstain drafts are authored by real writer-role LLM calls.
2. Stage B judgments are real content-derived outputs.
3. Duplicate/alignment/example semantics/modality gates pass.
4. Enforcement report status is `pass`.
5. Reviewer packet (if generated) comes from a passing run.
6. Human read confirms outputs are substantive, construct-specific, and non-boilerplate.

Final bar: a safety reviewer engages with technical substance rather than form defects.
