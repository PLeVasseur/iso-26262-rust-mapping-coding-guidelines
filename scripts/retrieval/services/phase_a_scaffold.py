from __future__ import annotations

# Uses shared helpers/constants from s0_phase_a_impl during transition split.
from retrieval.services.s0_phase_a_impl import *  # noqa: F403

def run_scaffold_s0_config(args: Namespace, *, root: Path) -> int:
    config_root = (root / "config" / "s0").resolve()
    config_root.mkdir(parents=True, exist_ok=True)
    overwrite = bool(getattr(args, "overwrite", False))

    files: dict[str, dict[str, Any]] = {
        "drafting_prompt_contract.yaml": {
            "version": 1,
            "synthesis_version": "s0-v1",
            "two_phase_triggers": {
                "draftability_not_strong": True,
                "hard_rows": ["1e", "1h", "1i"],
            },
            "worked_positive_examples": [
                {
                    "id": "pos_overflow_checked",
                    "row": "1e",
                    "safety_hazard": "Silent integer overflow in release builds can corrupt safety-relevant calculations.",
                    "construct_behavior": "Operator arithmetic may wrap in release; checked/saturating APIs provide explicit behavior.",
                    "mitigation_claim": "Safety-critical arithmetic shall use checked_*, saturating_*, or wrapping_* with explicit policy.",
                    "strength_justification": "shall is required because silent wrap can propagate undetected faults.",
                },
                {
                    "id": "pos_refcell_try",
                    "row": "1d",
                    "safety_hazard": "Runtime borrow panics can trigger unexpected control-flow failures.",
                    "construct_behavior": "RefCell::borrow_mut panics on conflicting borrows; try_borrow_mut returns Result-like control.",
                    "mitigation_claim": "Prefer non-panicking borrow APIs in critical paths and handle failure explicitly.",
                    "strength_justification": "shall where panic-induced abort violates fault containment expectations.",
                },
                {
                    "id": "pos_atomic_ordering",
                    "row": "1h",
                    "safety_hazard": "Incorrect atomic ordering can invalidate synchronization assumptions.",
                    "construct_behavior": "Acquire/Release/SeqCst semantics differ materially for visibility and ordering guarantees.",
                    "mitigation_claim": "Atomic APIs shall declare and justify memory ordering choices for each shared-state transition.",
                    "strength_justification": "shall because weak ordering misuse causes non-deterministic safety faults.",
                },
            ],
            "worked_negative_examples": [
                {
                    "id": "neg_generic_warning",
                    "row": "1d",
                    "safety_hazard": "This might be risky.",
                    "construct_behavior": "Rust has several relevant features.",
                    "mitigation_claim": "Developers should use best practices.",
                    "strength_justification": "good practice",
                },
                {
                    "id": "neg_tautological",
                    "row": "1e",
                    "safety_hazard": "Unsafe code can be unsafe.",
                    "construct_behavior": "Unsafe blocks require care.",
                    "mitigation_claim": "Use unsafe safely.",
                    "strength_justification": "safety",
                },
                {
                    "id": "neg_uncited_semantics",
                    "row": "1h",
                    "safety_hazard": "Thread bugs happen.",
                    "construct_behavior": "Concurrency is complex.",
                    "mitigation_claim": "Prefer stronger orderings.",
                    "strength_justification": "better reliability",
                },
            ],
            "significance_anchors": {
                "0": "doc restatement",
                "1": "obvious best practice",
                "2": "real issue but generic mitigation",
                "3": "specific hazard and concrete mitigation",
                "4": "non-obvious semantics+safety interaction",
                "5": "high-value incident-preventing guidance",
            },
        },
        "enforcement_catalog_s0.yaml": {
            "version": 1,
            "entries": [
                {
                    "id": "enf-clippy",
                    "smoke_check_safe": True,
                    "command": "cargo clippy --all-targets -- -D warnings",
                    "artifact": "clippy.log",
                },
                {
                    "id": "enf-fmt",
                    "smoke_check_safe": True,
                    "command": "cargo fmt --check",
                    "artifact": "fmt.log",
                },
                {
                    "id": "enf-miri",
                    "smoke_check_safe": False,
                    "command": "cargo miri test",
                    "artifact": "miri.log",
                },
                {
                    "id": "enf-deny-unsafe",
                    "smoke_check_safe": True,
                    "command": 'rg -n "unsafe\\s*\\{" src',
                    "artifact": "unsafe_scan.log",
                },
                {
                    "id": "enf-doc-tests",
                    "smoke_check_safe": True,
                    "command": "cargo test --doc",
                    "artifact": "doctest.log",
                },
                {
                    "id": "enf-build-offline",
                    "smoke_check_safe": True,
                    "command": "./make.py --offline",
                    "artifact": "build.log",
                },
                {
                    "id": "enf-rustc-warnings",
                    "smoke_check_safe": True,
                    "command": "cargo check --all-targets",
                    "artifact": "check.log",
                },
                {
                    "id": "enf-example-harness",
                    "smoke_check_safe": False,
                    "command": "python scripts/extract_rust_examples.py --test",
                    "artifact": "examples.log",
                },
            ],
        },
        "verification_catalog_s0.yaml": {
            "version": 1,
            "entries": [
                {
                    "id": "ver-unit-tests",
                    "smoke_check_safe": True,
                    "command": "cargo test",
                    "artifact": "test.log",
                },
                {
                    "id": "ver-property",
                    "smoke_check_safe": False,
                    "command": "cargo test --tests",
                    "artifact": "property.log",
                },
                {
                    "id": "ver-smoke",
                    "smoke_check_safe": True,
                    "command": "uv run python scripts/sqlite_kb.py smoke --corpus rust_reference",
                    "artifact": "smoke.log",
                },
                {
                    "id": "ver-validate",
                    "smoke_check_safe": True,
                    "command": "uv run python scripts/sqlite_kb.py validate --corpus rust_reference",
                    "artifact": "validate.log",
                },
                {
                    "id": "ver-eval-core",
                    "smoke_check_safe": False,
                    "command": "uv run python scripts/sqlite_kb.py eval --corpus core_docs",
                    "artifact": "eval_core.log",
                },
                {
                    "id": "ver-eval-ref",
                    "smoke_check_safe": False,
                    "command": "uv run python scripts/sqlite_kb.py eval --corpus rust_reference",
                    "artifact": "eval_ref.log",
                },
                {
                    "id": "ver-query-snapshot",
                    "smoke_check_safe": True,
                    "command": "uv run python scripts/sqlite_kb.py query --corpus core_docs -- --query-id snapshot_metadata --mode contract",
                    "artifact": "query.log",
                },
                {
                    "id": "ver-git-clean",
                    "smoke_check_safe": True,
                    "command": "git status --short",
                    "artifact": "git_status.log",
                },
            ],
        },
        "s0_targets.yaml": {
            "version": 1,
            "manual_overrides": {},
            "heuristic": {
                "strong_min_evidence": 4,
                "partial_min_evidence": 2,
                "auto_downgrade_after_abstains": 2,
            },
        },
        "s0_gate_policy.yaml": {
            "version": 1,
            "rewrite_mode": "auto",
            "top_k": 10,
            "candidate_limit": 5000,
            "convention_retry_budget": 50,
            "compilation_retry_budget": 15,
            "max_convention_retries": 50,
            "max_compilation_retries": 15,
            "max_judge_calls": 70,
            "dedup": ["span_hash", "normalized_excerpt_hash"],
        },
        "examples_gate_policy.yaml": {
            "version": 1,
            "scope": "changed_chapters",
            "publishable_requires_full_once": True,
            "full_fallback_threshold": 25,
            "periodic_full_schedule": "weekly",
        },
        "writer_prompt_contracts.yaml": {
            "contract_version": 1,
            "roles": {
                "evidence_synthesizer": {
                    "prompt_template_id": "writer-evidence-synth-v1",
                    "prompt_template_text": "You are the Evidence Synthesizer. Target {{target_id}} row {{table1_row}}. Use evidence IDs {{evidence_ids}} and exemplar IDs {{exemplar_ids}}. Return JSON only.",
                    "allowed_placeholders": [
                        "target_id",
                        "table1_row",
                        "corpus",
                        "evidence_ids",
                        "evidence_snippets",
                        "exemplar_ids",
                        "global_rules",
                    ],
                    "required_inputs": [
                        "target_metadata",
                        "evidence_bundle",
                        "exemplar_selection",
                        "style_context.global_rules",
                    ],
                    "required_output_schema": {
                        "required": [
                            "target_id",
                            "hazard",
                            "mechanism",
                            "mitigation",
                            "construct_scope",
                            "evidence_ids",
                            "claim_to_evidence_map",
                        ]
                    },
                    "forbidden_patterns": [
                        "uncited factual claim",
                        "final guideline prose",
                    ],
                    "style_rules_ref": ["global_rules"],
                    "abstain_policy": "emit reasoned abstain when evidence insufficient",
                },
                "amplification_author": {
                    "prompt_template_id": "writer-amplification-v1",
                    "prompt_template_text": "You are the Amplification Author. Write only the guideline body text for {{target_id}} using evidence synthesis and exemplars {{exemplar_ids}}. Return JSON only.",
                    "allowed_placeholders": [
                        "target_id",
                        "table1_row",
                        "evidence_synthesis",
                        "exemplar_ids",
                        "amplification_rules",
                    ],
                    "required_inputs": [
                        "evidence_synthesizer_output",
                        "exemplar_selection",
                        "style_context.global_rules",
                        "style_context.amplification_rules",
                    ],
                    "required_output_schema": {
                        "required": [
                            "target_id",
                            "guideline_amplification_text",
                            "normative_strength",
                            "amplification_citation_keys",
                        ]
                    },
                    "forbidden_patterns": [
                        "apply explicit controls",
                        "generic cross-target boilerplate",
                    ],
                    "style_rules_ref": ["global_rules", "amplification_rules"],
                    "abstain_policy": "emit abstain output with empty amplification citations",
                },
                "example_author": {
                    "prompt_template_id": "writer-example-v1",
                    "prompt_template_text": "You are the Example Author. Produce non-compliant and compliant narrative+code for {{target_id}} tied to the same hazard/construct family. Return JSON only.",
                    "allowed_placeholders": [
                        "target_id",
                        "evidence_synthesis",
                        "amplification",
                        "exemplar_ids",
                        "example_rules",
                    ],
                    "required_inputs": [
                        "evidence_synthesizer_output",
                        "amplification_author_output",
                        "exemplar_selection",
                        "style_context.global_rules",
                        "style_context.example_rules",
                    ],
                    "required_output_schema": {
                        "required": [
                            "target_id",
                            "non_compliant_narrative",
                            "non_compliant_code",
                            "compliant_narrative",
                            "compliant_code",
                            "example_citation_keys",
                        ]
                    },
                    "forbidden_patterns": [
                        "template stub code",
                        "unrelated construct examples",
                    ],
                    "style_rules_ref": ["global_rules", "example_rules"],
                    "abstain_policy": "emit abstain with empty example fields",
                },
                "rationale_author": {
                    "prompt_template_id": "writer-rationale-v1",
                    "prompt_template_text": "You are the Rationale Author. Produce hazard->mechanism->consequence rationale for {{target_id}}. Return JSON only.",
                    "allowed_placeholders": [
                        "target_id",
                        "evidence_synthesis",
                        "examples",
                        "rationale_rules",
                    ],
                    "required_inputs": [
                        "evidence_synthesizer_output",
                        "example_author_output",
                        "style_context.global_rules",
                        "style_context.rationale_rules",
                    ],
                    "required_output_schema": {
                        "required": [
                            "target_id",
                            "rationale_text",
                            "hazard_mechanism_consequence_map",
                            "rationale_citation_keys",
                        ]
                    },
                    "forbidden_patterns": [
                        "tautological rationale",
                        "generic non-causal language",
                    ],
                    "style_rules_ref": ["global_rules", "rationale_rules"],
                    "abstain_policy": "emit abstain rationale reason",
                },
                "metadata_citation_curator": {
                    "prompt_template_id": "writer-metadata-citation-v1",
                    "prompt_template_text": "You are the Metadata/Citation Curator. Produce style-conformant metadata and bibliography rows for {{target_id}}. Return JSON only.",
                    "allowed_placeholders": [
                        "target_id",
                        "all_writer_outputs",
                        "exemplar_metadata_patterns",
                        "metadata_bibliography_rules",
                    ],
                    "required_inputs": [
                        "evidence_synthesizer_output",
                        "amplification_author_output",
                        "example_author_output",
                        "rationale_author_output",
                        "style_context.global_rules",
                        "style_context.metadata_bibliography_rules",
                    ],
                    "required_output_schema": {
                        "required": [
                            "target_id",
                            "tags",
                            "fls_candidate",
                            "bibliography_rows",
                            "citation_key_map",
                            "metadata_validation_notes",
                        ]
                    },
                    "forbidden_patterns": [
                        "generic metadata defaults",
                        "invalid example or bibliography id conventions",
                    ],
                    "style_rules_ref": ["global_rules", "metadata_bibliography_rules"],
                    "abstain_policy": "emit abstain metadata with explicit note",
                },
            },
        },
        "judge_prompt_contracts.yaml": {
            "contract_version": 1,
            "roles": {
                "evidence_auditor": {
                    "prompt_template_id": "judge-evidence-v1",
                    "prompt_template_text": "Evaluate evidence grounding only for draft {{draft_id}} using evidence bundle {{evidence_ids}}. Return JSON only.",
                    "allowed_placeholders": [
                        "draft_id",
                        "draft_record",
                        "evidence_ids",
                        "evidence_bundle",
                    ],
                    "required_inputs": ["drafts_jsonl", "evidence_bundle"],
                    "required_output_schema": {"required": ["pass", "results"]},
                    "forbidden_patterns": ["style-only judgment"],
                },
                "functional_safety_relevance": {
                    "prompt_template_id": "judge-safety-v1",
                    "prompt_template_text": "Evaluate functional safety relevance and significance for {{draft_id}}. Return JSON only.",
                    "allowed_placeholders": ["draft_id", "draft_record", "significance_anchors"],
                    "required_inputs": ["drafts_jsonl", "significance_anchors"],
                    "required_output_schema": {"required": ["pass", "results"]},
                    "forbidden_patterns": ["evidence grounding assertions without evidence pass"],
                },
                "usability_actionability": {
                    "prompt_template_id": "judge-usability-v1",
                    "prompt_template_text": "Evaluate usability/actionability for {{draft_id}} and return JSON only.",
                    "allowed_placeholders": ["draft_id", "draft_record", "style_rules"],
                    "required_inputs": ["drafts_jsonl", "style_context_bundle"],
                    "required_output_schema": {"required": ["pass", "results"]},
                    "forbidden_patterns": ["candidate verdict assignment"],
                },
                "golden_shape_comparator": {
                    "prompt_template_id": "judge-shape-v1",
                    "prompt_template_text": "Compare generated draft {{draft_file}} against nearest exemplar {{exemplar_file}} and style rules. Return JSON only.",
                    "allowed_placeholders": ["draft_file", "exemplar_file", "style_rules"],
                    "required_inputs": [
                        "generated_guidelines_rst",
                        "curated_exemplars",
                        "style_guideline",
                    ],
                    "required_output_schema": {"required": ["results", "all_non_abstain_pass"]},
                    "forbidden_patterns": ["content-only pass without style and shape checks"],
                },
                "exemplar_usage_auditor": {
                    "prompt_template_id": "judge-exemplar-usage-v1",
                    "prompt_template_text": "Validate exemplar usage trace for target {{target_id}}. Return JSON only.",
                    "allowed_placeholders": ["target_id", "selection_trace", "synthesis_trace"],
                    "required_inputs": [
                        "exemplar_selection_trace",
                        "synthesis_input_trace",
                        "golden_exemplar_lock_report",
                    ],
                    "required_output_schema": {"required": ["run_id", "status", "results"]},
                    "forbidden_patterns": ["candidate verdict assignment"],
                },
                "writer_output_auditor": {
                    "prompt_template_id": "judge-writer-output-v1",
                    "prompt_template_text": "Validate writer role outputs for draft {{draft_id}}. Return JSON only.",
                    "allowed_placeholders": ["draft_id", "writer_outputs", "style_rules"],
                    "required_inputs": [
                        "writer_subagent_outputs",
                        "drafts_jsonl",
                        "style_context_bundle",
                    ],
                    "required_output_schema": {"required": ["run_id", "status", "results"]},
                    "forbidden_patterns": ["candidate verdict assignment"],
                },
                "holistic_pairwise": {
                    "prompt_template_id": "judge-holistic-v1",
                    "prompt_template_text": "Aggregate prior judge outputs for draft {{draft_id}} under candidate policy. Return JSON only.",
                    "allowed_placeholders": [
                        "draft_id",
                        "evidence_pass",
                        "safety_pass",
                        "usability_pass",
                        "shape_pass",
                        "usage_audit",
                        "writer_audit",
                    ],
                    "required_inputs": ["judge_pass_artifacts"],
                    "required_output_schema": {"required": ["pass", "results", "aggregate"]},
                    "forbidden_patterns": ["candidate verdict without policy prerequisites"],
                },
            },
        },
        "roles_required.yaml": {
            "version": 1,
            "roles": [
                "evidence_synthesizer",
                "amplification_author",
                "example_author",
                "rationale_author",
                "metadata_citation_curator",
            ],
        },
        "judges_required.yaml": {
            "version": 1,
            "judges": [
                "evidence_auditor",
                "functional_safety_relevance",
                "usability_actionability",
                "golden_shape_comparator",
                "exemplar_usage_auditor",
                "writer_output_auditor",
                "holistic_pairwise",
            ],
        },
        "anchor_expectation.yaml": {
            "version": 1,
            "defaults": {
                "anchor_required": False,
            },
            "corpora": {
                "rust_reference": {"anchor_required": True},
                "core_docs": {"anchor_required": False},
            },
        },
    }

    written: list[str] = []
    for name, payload in files.items():
        path = config_root / name
        if path.exists() and not overwrite:
            continue
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        written.append(str(path.relative_to(root)))

    print(
        json.dumps(
            {
                "operation": "scaffold-s0-config",
                "config_root": str(config_root),
                "written_count": len(written),
                "written": written,
            },
            indent=2,
        )
    )
    return EXIT_SUCCESS

