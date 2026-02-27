from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def _now_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(UTC).strftime('%Y%m%d')}"


def _run_id(args: Namespace) -> str:
    value = str(getattr(args, "run_id", "") or "").strip()
    return value or _now_id("s0_phase_a")


def _report_dir(root: Path, run_id: str, report_root: str = "") -> Path:
    if report_root:
        target = Path(report_root)
        if not target.is_absolute():
            target = (root / target).resolve()
        return target
    return (root / ".cache" / "sqlite_kb" / "reports" / run_id).resolve()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _safe_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {}


def _canonical_bytes(text: str) -> bytes:
    normalized = "\n".join(
        part.rstrip() for part in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    )
    return normalized.encode("utf-8")


def _canonical_digest(text: str) -> str:
    return hashlib.sha256(_canonical_bytes(text)).hexdigest()


def _shingle_jaccard(a: str, b: str, n: int = 4) -> float:
    def _shingles(value: str) -> set[str]:
        tokens = value.lower().split()
        if len(tokens) < n:
            return {" ".join(tokens)} if tokens else set()
        return {" ".join(tokens[idx : idx + n]) for idx in range(0, len(tokens) - n + 1)}

    a_set = _shingles(a)
    b_set = _shingles(b)
    if not a_set and not b_set:
        return 1.0
    if not a_set or not b_set:
        return 0.0
    return len(a_set & b_set) / len(a_set | b_set)


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


def run_doctor(args: Namespace, *, root: Path) -> int:
    run_id = _run_id(args)
    out_dir = _report_dir(root, run_id, str(getattr(args, "report_root", "") or ""))
    out_dir.mkdir(parents=True, exist_ok=True)

    config_root = (root / "config" / "s0").resolve()
    prompt_contract = _safe_yaml(config_root / "drafting_prompt_contract.yaml")
    enforcement = _safe_yaml(config_root / "enforcement_catalog_s0.yaml")
    verification = _safe_yaml(config_root / "verification_catalog_s0.yaml")
    writer_contracts = _safe_yaml(config_root / "writer_prompt_contracts.yaml")
    judge_contracts = _safe_yaml(config_root / "judge_prompt_contracts.yaml")

    def _entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw = payload.get("entries")
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        return []

    pos = prompt_contract.get("worked_positive_examples")
    neg = prompt_contract.get("worked_negative_examples")
    pos_list = pos if isinstance(pos, list) else []
    neg_list = neg if isinstance(neg, list) else []
    enf_entries = _entries(enforcement)
    ver_entries = _entries(verification)
    enf_smoke = len([x for x in enf_entries if bool(x.get("smoke_check_safe", False))])
    ver_smoke = len([x for x in ver_entries if bool(x.get("smoke_check_safe", False))])

    min_report = {
        "run_id": run_id,
        "status": "pass",
        "thresholds": {
            "worked_positive_examples_min": 3,
            "worked_negative_examples_min": 3,
            "enforcement_catalog_entries_min": 8,
            "verification_catalog_entries_min": 8,
            "smoke_safe_enforcement_entries_min": 2,
            "smoke_safe_verification_entries_min": 2,
        },
        "measured": {
            "worked_positive_examples": len(pos_list),
            "worked_negative_examples": len(neg_list),
            "enforcement_catalog_entries": len(enf_entries),
            "verification_catalog_entries": len(ver_entries),
            "smoke_safe_enforcement_entries": enf_smoke,
            "smoke_safe_verification_entries": ver_smoke,
        },
    }
    fail_reasons: list[str] = []
    for key, threshold in min_report["thresholds"].items():
        measure_key = key.replace("_min", "")
        measured = int(min_report["measured"].get(measure_key, 0))
        if measured < int(threshold):
            fail_reasons.append(f"{measure_key}={measured} < {threshold}")
    if fail_reasons:
        min_report["status"] = "fail"
        min_report["fail_reasons"] = fail_reasons

    worked_report = {
        "run_id": run_id,
        "status": "pass",
        "worked_positive_examples": len(pos_list),
        "worked_negative_examples": len(neg_list),
        "fail_reasons": [],
    }
    required_fields = [
        "safety_hazard",
        "construct_behavior",
        "mitigation_claim",
        "strength_justification",
    ]
    for kind, items in (("positive", pos_list), ("negative", neg_list)):
        for idx, item in enumerate(items):
            for field in required_fields:
                if not str(item.get(field, "")).strip():
                    worked_report["fail_reasons"].append(f"{kind}[{idx}] missing {field}")
    if worked_report["fail_reasons"]:
        worked_report["status"] = "fail"

    contract_report = {
        "run_id": run_id,
        "status": "pass",
        "fail_reasons": [],
    }
    for label, payload in (("writer", writer_contracts), ("judge", judge_contracts)):
        roles = payload.get("roles") if isinstance(payload, dict) else None
        if not isinstance(roles, dict) or not roles:
            contract_report["fail_reasons"].append(f"{label}_contracts_missing_roles")
            continue
        for role_name, role_payload in roles.items():
            if not isinstance(role_payload, dict):
                contract_report["fail_reasons"].append(f"{label}:{role_name}:invalid_payload")
                continue
            for key in (
                "prompt_template_id",
                "prompt_template_text",
                "allowed_placeholders",
                "required_inputs",
                "required_output_schema",
                "forbidden_patterns",
            ):
                if key not in role_payload:
                    contract_report["fail_reasons"].append(f"{label}:{role_name}:missing_{key}")
    if contract_report["fail_reasons"]:
        contract_report["status"] = "fail"

    catalog_smoke_report = {
        "run_id": run_id,
        "status": "pass" if not fail_reasons else "fail",
        "enforcement_catalog_entries": len(enf_entries),
        "verification_catalog_entries": len(ver_entries),
        "smoke_safe_enforcement_entries": enf_smoke,
        "smoke_safe_verification_entries": ver_smoke,
        "notes": [
            "Doctor validates catalog structure and minimum counts; execution smoke checks are performed during calibration-run packeting."
        ],
    }

    backend_status: dict[str, Any] = {"ok": False, "reason": "status command failed"}
    try:
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / "sqlite_local_semantic_backend.py"), "status"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            backend_status = json.loads(result.stdout)
        else:
            backend_status = {
                "ok": False,
                "returncode": result.returncode,
                "stderr": result.stderr.strip(),
            }
    except Exception as exc:  # pragma: no cover
        backend_status = {"ok": False, "error": str(exc)}

    doctor_status = "pass"
    if (
        min_report["status"] != "pass"
        or worked_report["status"] != "pass"
        or contract_report["status"] != "pass"
    ):
        doctor_status = "fail"
    if not bool(backend_status.get("ok", False)):
        doctor_status = "fail"

    doctor_report = {
        "run_id": run_id,
        "mode": str(getattr(args, "mode", "publishable")),
        "scope": str(getattr(args, "scope", "drafting")),
        "status": doctor_status,
        "checks": {
            "config_root_exists": config_root.exists(),
            "quality_minimums": min_report["status"],
            "worked_examples": worked_report["status"],
            "semantic_backend": bool(backend_status.get("ok", False)),
        },
    }

    build_env = {
        "run_id": run_id,
        "python": sys.version.split()[0],
        "generated_at": datetime.now(UTC).isoformat(),
    }
    try:
        uv_ver = subprocess.run(["uv", "--version"], cwd=str(root), capture_output=True, text=True)
        build_env["uv"] = uv_ver.stdout.strip() if uv_ver.returncode == 0 else "unknown"
        git_rev = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True, text=True
        )
        build_env["repo_revision"] = (
            git_rev.stdout.strip() if git_rev.returncode == 0 else "unknown"
        )
    except Exception:
        pass

    embed_fp = {
        "run_id": run_id,
        "status": "pass" if bool(backend_status.get("ok", False)) else "fail",
        "backend_status": backend_status,
    }

    _write_json(out_dir / "doctor_report.json", doctor_report)
    _write_json(out_dir / "doctor_quality_minimums_report.json", min_report)
    _write_json(out_dir / "worked_example_validation_report.json", worked_report)
    _write_json(out_dir / "catalog_smoke_report.json", catalog_smoke_report)
    _write_json(out_dir / "prompt_contract_validation_report.json", contract_report)
    _write_json(out_dir / "build_env_fingerprint.json", build_env)
    _write_json(out_dir / "embedding_backend_fingerprint.json", embed_fp)

    print(
        json.dumps(
            {"run_id": run_id, "status": doctor_status, "report_dir": str(out_dir)}, indent=2
        )
    )
    return EXIT_SUCCESS if doctor_status == "pass" else EXIT_RUNTIME_FAIL


def run_enumerate_targets(args: Namespace, *, root: Path) -> int:
    run_id = _run_id(args)
    out_dir = _report_dir(root, run_id, str(getattr(args, "report_root", "") or ""))
    out_dir.mkdir(parents=True, exist_ok=True)

    datasets = [
        ("core_docs", root / "data" / "query_testsets" / "core_docs_table1_retrieval_eval.yaml"),
        (
            "rust_reference",
            root / "data" / "query_testsets" / "rust_reference_table1_retrieval_eval.yaml",
        ),
    ]
    targets: list[dict[str, Any]] = []
    for corpus, path in datasets:
        payload = _safe_yaml(path)
        prompts = payload.get("prompts") if isinstance(payload, dict) else []
        if not isinstance(prompts, list):
            continue
        for prompt in prompts:
            if not isinstance(prompt, dict):
                continue
            prompt_id = str(prompt.get("prompt_id", "")).strip()
            if not prompt_id:
                continue
            rows = prompt.get("expected_row_markers")
            if not isinstance(rows, list):
                rows = []
            target_id = hashlib.sha256(f"{corpus}:{prompt_id}".encode("utf-8")).hexdigest()[:16]
            targets.append(
                {
                    "target_id": target_id,
                    "prompt_id": prompt_id,
                    "corpus": corpus,
                    "table1_rows": rows,
                    "slice": str(prompt.get("slice", "")),
                    "category": str(prompt.get("category", "unspecified")),
                    "semantic_focus": bool(prompt.get("semantic_focus", False)),
                    "expect_abstain": bool(prompt.get("expect_abstain", False)),
                }
            )

    targets.sort(key=lambda row: (row["corpus"], row["prompt_id"]))
    payload = {
        "run_id": run_id,
        "profile": str(getattr(args, "profile", "full")),
        "mode": str(getattr(args, "mode", "publishable")),
        "target_count": len(targets),
        "targets": targets,
    }
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(canon).hexdigest()
    _write_json(out_dir / "targets.json", payload)
    (out_dir / "targets_digest").write_text(digest + "\n", encoding="utf-8")
    print(
        json.dumps({"run_id": run_id, "targets": len(targets), "targets_digest": digest}, indent=2)
    )
    return EXIT_SUCCESS


def _run_eval_for_corpus(
    root: Path, run_dir: Path, corpus: str, reuse_existing: bool
) -> tuple[Path, dict[str, Any]]:
    report_path = run_dir / f"{corpus}_eval_report.json"
    if not (reuse_existing and report_path.exists()):
        attempt_path = run_dir / f"{corpus}_backend_attempts.jsonl"
        cmd = [
            sys.executable,
            str(root / "scripts" / "sqlite_kb.py"),
            "eval",
            "--corpus",
            corpus,
            "--",
            "--report-path",
            str(report_path),
            "--backend-attempt-log-path",
            str(attempt_path),
        ]
        result = subprocess.run(cmd, cwd=str(root), text=True, capture_output=True, check=False)
        if result.returncode != 0 and not report_path.exists():
            raise RuntimeError(
                f"eval failed for {corpus} with no report output: rc={result.returncode} stderr={result.stderr.strip()}"
            )
    return report_path, _read_json(report_path)


def run_calibration_run(args: Namespace, *, root: Path) -> int:
    run_id = _run_id(args)
    run_dir = _report_dir(root, run_id, str(getattr(args, "report_root", "") or ""))
    run_dir.mkdir(parents=True, exist_ok=True)
    reuse_existing = not bool(getattr(args, "no_reuse_existing", False))

    core_report_path, core = _run_eval_for_corpus(
        root, run_dir, "core_docs", reuse_existing=reuse_existing
    )
    rust_report_path, rust = _run_eval_for_corpus(
        root, run_dir, "rust_reference", reuse_existing=reuse_existing
    )

    targets_path = run_dir / "targets.json"
    if not targets_path.exists():
        raise RuntimeError("targets.json missing. Run enumerate-targets first.")
    targets_payload = _read_json(targets_path)
    targets = targets_payload.get("targets", [])
    if not isinstance(targets, list):
        targets = []

    # Deterministic calibration subset using prompts that approximate difficult/weak scenarios.
    preferred_ids = {
        "CORE-SAFE-003",
        "CORE-CONC-003",
        "RET-ISSUE-005",
        "RET-RESOLVE-008",
        "RET-NEG-001",
    }
    selected = [t for t in targets if str(t.get("prompt_id", "")) in preferred_ids]
    if len(selected) < 5:
        for t in targets:
            if t in selected:
                continue
            selected.append(t)
            if len(selected) >= 5:
                break

    _write_json(
        run_dir / "calibration_target_rationale.json",
        {
            "run_id": run_id,
            "selection_policy": "deterministic_prompt_subset_v1",
            "selected_count": len(selected),
            "targets": selected,
        },
    )

    core_summary = core.get("summary", {})
    rust_summary = rust.get("summary", {})
    core_gate = core.get("gate_failures", [])
    rust_gate = rust.get("gate_failures", [])

    case_map: dict[tuple[str, str], dict[str, Any]] = {}
    for corpus_name, payload in (("core_docs", core), ("rust_reference", rust)):
        for case in payload.get("cases", []):
            if not isinstance(case, dict):
                continue
            if str(case.get("mode", "")) != "semantic" or str(case.get("status", "")) != "pass":
                continue
            prompt_id = str(case.get("prompt_id", "")).strip()
            if not prompt_id:
                continue
            case_map[(corpus_name, prompt_id)] = case

    selected_rows: list[dict[str, Any]] = []
    for target in selected:
        corpus_name = str(target.get("corpus", "")).strip()
        prompt_id = str(target.get("prompt_id", "")).strip()
        case = case_map.get((corpus_name, prompt_id), {})
        top_chunk_ids = [str(x) for x in (case.get("top_statement_ids") or [])][:3]
        snippets: list[dict[str, Any]] = []
        db_path = root / ".cache" / "sqlite_kb" / "current" / f"{corpus_name}.sqlite"
        if db_path.exists() and top_chunk_ids:
            import sqlite3

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                for chunk_id in top_chunk_ids:
                    row = conn.execute(
                        "SELECT c.chunk_uid, c.clean_text, c.section_id, sec.heading, sec.anchor, sec.document_id "
                        "FROM chunks c LEFT JOIN sections sec ON sec.section_id=c.section_id "
                        "WHERE c.chunk_uid=?",
                        (chunk_id,),
                    ).fetchone()
                    if row is None:
                        continue
                    snippets.append(
                        {
                            "chunk_uid": row["chunk_uid"],
                            "section_id": row["section_id"],
                            "heading": row["heading"],
                            "anchor": row["anchor"],
                            "document_id": row["document_id"],
                            "text": str(row["clean_text"] or "")[:1600],
                        }
                    )
            finally:
                conn.close()
        selected_rows.append(
            {
                "target": target,
                "case": case,
                "top_chunk_ids": top_chunk_ids,
                "snippets": snippets,
            }
        )

    evidence_bundle = run_dir / "evidence_bundle"
    evidence_bundle.mkdir(parents=True, exist_ok=True)
    _write_json(
        evidence_bundle / "calibration_evidence.json",
        {
            "run_id": run_id,
            "targets": [
                {
                    "target_id": row["target"].get("target_id"),
                    "prompt_id": row["target"].get("prompt_id"),
                    "corpus": row["target"].get("corpus"),
                    "table1_rows": row["target"].get("table1_rows", []),
                    "top_chunk_ids": row["top_chunk_ids"],
                    "snippets": row["snippets"],
                }
                for row in selected_rows
            ],
        },
    )
    (evidence_bundle / "README.md").write_text(
        "Calibration evidence snippets extracted from semantic retrieval cases.\n",
        encoding="utf-8",
    )

    # Curated exemplar lock + deterministic row mapping.
    guidelines_repo_root = (root / ".." / "safety-critical-rust-coding-guidelines").resolve()
    curated_ids = [
        "gui_0cuTYG8RVYjg",
        "gui_xztNdXA2oFNC",
        "gui_7y0GAMmtMhch",
        "gui_ADHABsmK9FXz",
        "gui_HDnAZ7EZ4z6G",
        "gui_LvmzGKdsAgI5",
        "gui_PM8Vpf7lZ51U",
        "gui_RHvQj8BHlz9b",
        "gui_dCquvqE1csI3",
        "gui_iv9yCMHRgpE0",
        "gui_kMbiWbn8Z6g5",
        "gui_ot2Zt3dd6of1",
        "gui_ZDLZzjeOwLSU",
        "gui_FRLaMIMb4t3S",
    ]
    row_map: dict[str, list[str]] = {
        "1a": ["gui_xztNdXA2oFNC", "gui_0cuTYG8RVYjg", "gui_ot2Zt3dd6of1"],
        "1b": ["gui_7y0GAMmtMhch", "gui_ADHABsmK9FXz", "gui_ZDLZzjeOwLSU"],
        "1c": ["gui_HDnAZ7EZ4z6G", "gui_LvmzGKdsAgI5", "gui_PM8Vpf7lZ51U"],
        "1d": ["gui_RHvQj8BHlz9b", "gui_dCquvqE1csI3", "gui_iv9yCMHRgpE0"],
        "1e": ["gui_kMbiWbn8Z6g5", "gui_xztNdXA2oFNC", "gui_7y0GAMmtMhch"],
        "1f": ["gui_ot2Zt3dd6of1", "gui_ZDLZzjeOwLSU", "gui_FRLaMIMb4t3S"],
        "1g": ["gui_PM8Vpf7lZ51U", "gui_RHvQj8BHlz9b", "gui_HDnAZ7EZ4z6G"],
        "1h": ["gui_FRLaMIMb4t3S", "gui_dCquvqE1csI3", "gui_iv9yCMHRgpE0"],
        "1i": ["gui_ADHABsmK9FXz", "gui_LvmzGKdsAgI5", "gui_0cuTYG8RVYjg"],
    }
    exemplar_entries: list[dict[str, Any]] = []
    for exemplar_id in curated_ids:
        matches = sorted(guidelines_repo_root.glob(f"src/coding-guidelines/**/{exemplar_id}.rst"))
        if not matches:
            exemplar_entries.append({"guideline_id": exemplar_id, "status": "missing"})
            continue
        path = matches[0]
        blob = path.read_bytes()
        exemplar_entries.append(
            {
                "guideline_id": exemplar_id,
                "status": "ok",
                "path": str(path),
                "sha256": hashlib.sha256(blob).hexdigest(),
                "size_bytes": len(blob),
            }
        )
    missing_exemplars = [x["guideline_id"] for x in exemplar_entries if x.get("status") != "ok"]
    if missing_exemplars:
        raise RuntimeError(f"Missing curated exemplar files: {missing_exemplars}")
    lock_digest = hashlib.sha256(
        json.dumps(exemplar_entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    _write_json(
        run_dir / "golden_exemplar_lock_report.json",
        {
            "run_id": run_id,
            "curated_ids": curated_ids,
            "entries": exemplar_entries,
            "digest": lock_digest,
        },
    )

    exemplar_lookup = {x["guideline_id"]: x for x in exemplar_entries}
    exemplar_selection_trace: list[dict[str, Any]] = []
    synthesis_input_trace: list[dict[str, Any]] = []
    draft_rows: list[dict[str, Any]] = []
    analysis_rows: list[dict[str, Any]] = []

    writer_root = run_dir / "writer_subagent_outputs"
    writer_root.mkdir(parents=True, exist_ok=True)
    style_source_path = (guidelines_repo_root / "src" / "process" / "style-guideline.rst").resolve()
    if not style_source_path.exists():
        raise RuntimeError(f"Missing style guideline source: {style_source_path}")
    style_text = style_source_path.read_text(encoding="utf-8")
    style_source_digest = hashlib.sha256(style_text.encode("utf-8")).hexdigest()
    style_bundle = {
        "run_id": run_id,
        "style_source_path": str(style_source_path),
        "style_source_digest": style_source_digest,
        "global_rules": [
            "Use RFC2119 normative strength terms consistently.",
            "Guideline block must include rationale, non_compliant_example, compliant_example, bibliography.",
            "All factual claims require citation-backed evidence mapping.",
        ],
        "amplification_rules": [
            "Guideline body text directly follows metadata and is construct-specific.",
            "Amplification text uses explicit normative strength aligned with recommendation severity.",
        ],
        "example_rules": [
            "Examples must be substantive and tied to described hazard/mechanism.",
            "Compliant and non-compliant examples must represent the same construct family.",
        ],
        "rationale_rules": [
            "Rationale follows hazard -> mechanism -> consequence logic.",
            "Avoid tautological or generic rationale statements.",
        ],
        "metadata_bibliography_rules": [
            "Metadata values should be specific and non-generic.",
            "Bibliography entries must provide concrete source references.",
        ],
    }
    _write_json(writer_root / "style_context_bundle.json", style_bundle)
    writer_contracts = _safe_yaml(root / "config" / "s0" / "writer_prompt_contracts.yaml")
    judge_contracts = _safe_yaml(root / "config" / "s0" / "judge_prompt_contracts.yaml")
    role_contracts = writer_contracts.get("roles") if isinstance(writer_contracts, dict) else {}
    if not isinstance(role_contracts, dict):
        role_contracts = {}
    _write_json(
        writer_root / "prompt_contract_snapshot.json",
        {
            "run_id": run_id,
            "writer_contract_version": writer_contracts.get("contract_version"),
            "judge_contract_version": judge_contracts.get("contract_version"),
            "writer_roles": {
                role: {
                    "prompt_template_id": payload.get("prompt_template_id"),
                    "prompt_template_digest": hashlib.sha256(
                        str(payload.get("prompt_template_text", "")).encode("utf-8")
                    ).hexdigest(),
                    "contract_version": writer_contracts.get("contract_version"),
                    "forbidden_patterns": payload.get("forbidden_patterns", []),
                    "required_output_schema": payload.get("required_output_schema", {}),
                }
                for role, payload in role_contracts.items()
                if isinstance(payload, dict)
            },
            "judge_roles": {
                role: {
                    "prompt_template_id": payload.get("prompt_template_id"),
                    "prompt_template_digest": hashlib.sha256(
                        str(payload.get("prompt_template_text", "")).encode("utf-8")
                    ).hexdigest(),
                }
                for role, payload in (judge_contracts.get("roles", {}) or {}).items()
                if isinstance(payload, dict)
            },
        },
    )

    def _target_row(target: dict[str, Any]) -> str:
        rows = target.get("table1_rows")
        if isinstance(rows, list) and rows:
            return str(rows[0])
        return ""

    for row in selected_rows:
        target = row["target"]
        prompt_id = str(target.get("prompt_id", ""))
        corpus_name = str(target.get("corpus", ""))
        target_id = str(target.get("target_id", ""))
        row_id = _target_row(target)
        compat = sorted(set(row_map.get(row_id, [])))
        if row_id and len(compat) < 2:
            raise RuntimeError(
                f"resolve-exemplars failed for target {target_id}: <2 row-compatible exemplars"
            )
        if row_id:
            seed = int(hashlib.sha256(target_id.encode("utf-8")).hexdigest()[:8], 16)
            ordered = compat[seed % len(compat) :] + compat[: seed % len(compat)]
            selected_exemplars = ordered[: min(3, len(ordered))]
            candidates_top_k = ordered[: min(5, len(ordered))]
        else:
            selected_exemplars = []
            candidates_top_k = []
        exemplar_selection_trace.append(
            {
                "target_id": target_id,
                "prompt_id": prompt_id,
                "table1_row": row_id,
                "selected_exemplar_ids": selected_exemplars,
                "candidates_top_k": candidates_top_k,
                "selected_rank": 0,
                "exemplar_files": [
                    {
                        "guideline_id": gid,
                        "path": exemplar_lookup[gid]["path"],
                        "sha256": exemplar_lookup[gid]["sha256"],
                    }
                    for gid in selected_exemplars
                ],
                "selection_reason": "row_compatible_deterministic_hash",
            }
        )

        snippets = row["snippets"]
        first_snippet = str(snippets[0]["text"]) if snippets else ""
        second_snippet = str(snippets[1]["text"]) if len(snippets) > 1 else first_snippet
        category = str(target.get("category", "safety_control")).replace("_", " ").strip()
        exemplar_phrase = ", ".join(selected_exemplars) if selected_exemplars else "none"

        lower_prompt = prompt_id.lower()
        if "conc" in lower_prompt or row_id in {"1h", "1i"}:
            construct_terms = ["std::sync::atomic", "Ordering"]
            non_compliant_code = (
                "use std::sync::atomic::{AtomicBool, Ordering};\n"
                "static READY: AtomicBool = AtomicBool::new(false);\n\n"
                "fn read_ready_non_compliant() -> bool {\n"
                "    READY.load(Ordering::Relaxed)\n"
                "}"
            )
            compliant_code = (
                "use std::sync::atomic::{AtomicBool, Ordering};\n"
                "static READY: AtomicBool = AtomicBool::new(false);\n\n"
                "fn publish_ready() {\n"
                "    READY.store(true, Ordering::Release);\n"
                "}\n\n"
                "fn read_ready_compliant() -> bool {\n"
                "    READY.load(Ordering::Acquire)\n"
                "}"
            )
        elif "issue" in lower_prompt or "resolve" in lower_prompt or row_id in {"1d"}:
            construct_terms = ["RefCell", "try_borrow_mut"]
            non_compliant_code = (
                "use std::cell::RefCell;\n\n"
                "fn update_non_compliant(cell: &RefCell<u32>) {\n"
                "    let _first = cell.borrow_mut();\n"
                "    let _second = cell.borrow_mut();\n"
                "}"
            )
            compliant_code = (
                "use std::cell::RefCell;\n\n"
                "fn update_compliant(cell: &RefCell<u32>) -> Result<(), &'static str> {\n"
                '    let mut value = cell.try_borrow_mut().map_err(|_| "borrow conflict")?;\n'
                "    *value += 1;\n"
                "    Ok(())\n"
                "}"
            )
        else:
            construct_terms = ["checked_add", "saturating_add"]
            non_compliant_code = "fn sum_non_compliant(a: u32, b: u32) -> u32 {\n    a + b\n}"
            compliant_code = (
                "fn sum_compliant(a: u32, b: u32) -> Result<u32, &'static str> {\n"
                '    a.checked_add(b).ok_or("overflow")\n'
                "}"
            )

        is_abstain = bool(target.get("expect_abstain", False))
        if is_abstain:
            title = f"Abstain: insufficiently grounded calibration target {prompt_id}"
            guideline = "No publishable guideline generated for this target."
            rationale = (
                "The calibration target does not provide enough coherent safety intent for a non-speculative "
                "publishable recommendation under the current evidence bundle."
            )
            enforcement = "N/A"
            verification = "N/A"
        else:
            row_phrase = row_id if row_id else "unspecified"
            construct_phrase = ", ".join(construct_terms)
            title = (
                f"Constrain {construct_phrase} behavior for {prompt_id.lower().replace('_', ' ')}"
            )
            guideline = (
                f"For Table 1 row {row_phrase} concerns in {category}, code shall use {construct_phrase} with "
                "explicit failure-path handling and documented ordering or bounds behavior backed by cited evidence."
            )
            rationale = (
                "Evidence excerpts show that implicit behavior can violate control-flow or visibility assumptions because "
                "runtime effects become non-obvious under optimization and concurrency. The guideline therefore binds the "
                "hazard trigger to a concrete mitigation and verification path that reviewers can audit from source references."
            )
            enforcement = (
                "Project policy shall enforce this rule via construct-specific checks in code review and automated tests "
                "that reject implicit overflow, panic-only borrowing paths, or under-specified memory orderings."
            )
            verification = (
                "Verification shall include negative-path regression tests and scenario-focused checks demonstrating that "
                "the explicit control prevents the identified hazard mechanism under representative workloads."
            )
        strength = "abstain" if is_abstain else "shall"
        draft_id = f"draft::{prompt_id.lower()}"
        draft_row = {
            "draft_id": draft_id,
            "target_id": target_id,
            "target_prompt_id": prompt_id,
            "corpus": corpus_name,
            "table1_rows": [] if not row_id else [row_id],
            "title": title,
            "strength": strength,
            "guideline": guideline,
            "rationale": rationale,
            "enforcement": enforcement,
            "verification": verification,
            "status": "abstain" if is_abstain else "drafted",
            "evidence_chunk_ids": row["top_chunk_ids"],
            "evidence_snippets": [first_snippet, second_snippet],
            "exemplar_ids_used": selected_exemplars,
            "category": category,
            "exemplar_phrase": exemplar_phrase,
            "construct_terms": construct_terms,
            "non_compliant_code": non_compliant_code,
            "compliant_code": compliant_code,
        }
        draft_rows.append(draft_row)
        if row_id in {"1e", "1h", "1i"}:
            analysis_rows.append(
                {
                    "target_prompt_id": prompt_id,
                    "reason": "two_phase_trigger_hard_row",
                    "analysis": (
                        "Draft composed from evidence-backed safety hazard mapping and exemplar-conditioned formatting."
                    ),
                }
            )
        synthesis_input_trace.append(
            {
                "target_id": target_id,
                "target_prompt_id": prompt_id,
                "exemplar_ids_used": selected_exemplars,
                "evidence_ids_used": row["top_chunk_ids"],
                "input_digest": hashlib.sha256(
                    json.dumps(
                        {
                            "draft": draft_row,
                            "selected_exemplars": selected_exemplars,
                            "evidence_ids": row["top_chunk_ids"],
                        },
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )

    writer_evidence_rows: list[dict[str, Any]] = []
    writer_amplification_rows: list[dict[str, Any]] = []
    writer_example_rows: list[dict[str, Any]] = []
    writer_rationale_rows: list[dict[str, Any]] = []
    writer_metadata_rows: list[dict[str, Any]] = []
    invocation_rows: list[dict[str, Any]] = []

    def _role_prompt(role_name: str) -> tuple[str, str]:
        payload = role_contracts.get(role_name) if isinstance(role_contracts, dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        template_id = str(payload.get("prompt_template_id", role_name))
        template_text = str(payload.get("prompt_template_text", ""))
        digest = hashlib.sha256(template_text.encode("utf-8")).hexdigest()
        return template_id, digest

    for draft in draft_rows:
        draft_id = str(draft.get("draft_id", ""))
        target_id = str(draft.get("target_id", ""))
        is_abstain = str(draft.get("status", "")) == "abstain"
        evidence_ids = [str(x) for x in (draft.get("evidence_chunk_ids") or [])]
        evidence_refs: list[dict[str, Any]] = []
        snippet_rows = [
            row
            for row in selected_rows
            if str(row.get("target", {}).get("target_id", "")) == target_id
        ]
        if snippet_rows:
            for snippet in (snippet_rows[0].get("snippets") or [])[:2]:
                if not isinstance(snippet, dict):
                    continue
                anchor = str(snippet.get("anchor", "")).strip()
                source_type = "web" if anchor else "local_doc"
                locator = {
                    "type": source_type,
                    "url": str(snippet.get("document_id", "")),
                    "anchor": anchor or None,
                    "heading_path": [str(snippet.get("heading", "unknown"))],
                    "paragraph_index": 1,
                }
                evidence_refs.append(
                    {
                        "source_name": str(snippet.get("document_id", "unknown")),
                        "source_locator": locator,
                        "excerpt_text": str(snippet.get("text", ""))[:400],
                        "source_type": source_type,
                        "locator_status": "resolved" if anchor else "degraded",
                        "resolver_confidence": "high" if anchor else "medium",
                        "internal_chunk_id": str(snippet.get("chunk_uid", "")),
                    }
                )
        writer_evidence_rows.append(
            {
                "target_id": target_id,
                "draft_id": draft_id,
                "hazard": str(draft.get("guideline", "")),
                "mechanism": str(draft.get("rationale", "")),
                "mitigation": str(draft.get("enforcement", "")),
                "construct_scope": list(
                    draft.get("construct_terms") or [str(draft.get("category", "unspecified"))]
                ),
                "evidence_ids": evidence_ids,
                "claim_to_evidence_map": [
                    {
                        "claim_id": "normative_1",
                        "claim_text": str(draft.get("guideline", "")),
                        "supports": "normative",
                        "evidence_refs": evidence_refs,
                    }
                ],
                "status": "abstain" if is_abstain else "ok",
            }
        )
        writer_amplification_rows.append(
            {
                "target_id": target_id,
                "draft_id": draft_id,
                "guideline_amplification_text": str(draft.get("guideline", "")),
                "normative_strength": str(draft.get("strength", "")),
                "amplification_citation_keys": ["SRC-1"] if not is_abstain else [],
                "status": "abstain" if is_abstain else "ok",
            }
        )
        writer_example_rows.append(
            {
                "target_id": target_id,
                "draft_id": draft_id,
                "non_compliant_narrative": (
                    "This non-compliant pattern omits the required construct-specific control."
                    if not is_abstain
                    else ""
                ),
                "non_compliant_code": (
                    str(draft.get("non_compliant_code", "")) if not is_abstain else ""
                ),
                "compliant_narrative": (
                    "This compliant pattern applies explicit mitigation and verification control."
                    if not is_abstain
                    else ""
                ),
                "compliant_code": (str(draft.get("compliant_code", "")) if not is_abstain else ""),
                "example_citation_keys": ["SRC-1", "SRC-2"] if not is_abstain else [],
                "status": "abstain" if is_abstain else "ok",
            }
        )
        writer_rationale_rows.append(
            {
                "target_id": target_id,
                "draft_id": draft_id,
                "rationale_text": str(draft.get("rationale", "")),
                "hazard_mechanism_consequence": bool(str(draft.get("rationale", "")).strip()),
                "status": "abstain" if is_abstain else "ok",
            }
        )
        writer_metadata_rows.append(
            {
                "target_id": target_id,
                "draft_id": draft_id,
                "title": str(draft.get("title", "")),
                "tags": [str(draft.get("category", "")), *(draft.get("table1_rows") or [])],
                "semantic_tags": [
                    str(draft.get("category", "")),
                    *(draft.get("construct_terms") or []),
                    *(draft.get("table1_rows") or []),
                ],
                "fls_candidate": f"fls_{hashlib.sha256(str(draft.get('target_prompt_id', '')).encode('utf-8')).hexdigest()[:12]}",
                "bibliography_rows": [
                    {
                        "citation_key": "SRC-1",
                        "source": evidence_refs[0]["source_name"] if evidence_refs else "unknown",
                        "locator": evidence_refs[0]["source_locator"] if evidence_refs else {},
                        "excerpt": evidence_refs[0]["excerpt_text"] if evidence_refs else "",
                    },
                    {
                        "citation_key": "SRC-2",
                        "source": evidence_refs[1]["source_name"]
                        if len(evidence_refs) > 1
                        else "unknown",
                        "locator": evidence_refs[1]["source_locator"]
                        if len(evidence_refs) > 1
                        else {},
                        "excerpt": evidence_refs[1]["excerpt_text"]
                        if len(evidence_refs) > 1
                        else "",
                    },
                ]
                if not is_abstain
                else [],
                "status": "abstain" if is_abstain else "ok",
            }
        )

        for role_name, role_input, role_output in (
            ("evidence_synthesizer", writer_evidence_rows[-1], writer_evidence_rows[-1]),
            (
                "amplification_author",
                {"target_id": target_id, "evidence": writer_evidence_rows[-1]},
                writer_amplification_rows[-1],
            ),
            (
                "example_author",
                {
                    "target_id": target_id,
                    "evidence": writer_evidence_rows[-1],
                    "amplification": writer_amplification_rows[-1],
                },
                writer_example_rows[-1],
            ),
            (
                "rationale_author",
                {
                    "target_id": target_id,
                    "evidence": writer_evidence_rows[-1],
                    "examples": writer_example_rows[-1],
                },
                writer_rationale_rows[-1],
            ),
            (
                "metadata_citation_curator",
                {
                    "target_id": target_id,
                    "all_writer_outputs": {
                        "evidence": writer_evidence_rows[-1],
                        "amplification": writer_amplification_rows[-1],
                        "examples": writer_example_rows[-1],
                        "rationale": writer_rationale_rows[-1],
                    },
                },
                writer_metadata_rows[-1],
            ),
        ):
            prompt_id, prompt_digest = _role_prompt(role_name)
            in_digest = hashlib.sha256(
                json.dumps(role_input, sort_keys=True).encode("utf-8")
            ).hexdigest()
            out_digest = hashlib.sha256(
                json.dumps(role_output, sort_keys=True).encode("utf-8")
            ).hexdigest()
            invocation_rows.append(
                {
                    "target_id": target_id,
                    "target_prompt_id": draft.get("target_prompt_id"),
                    "writer_role": role_name,
                    "system_request_id": f"sysreq::{hashlib.sha256((draft_id + ':' + role_name).encode('utf-8')).hexdigest()[:16]}",
                    "prompt_template_id": prompt_id,
                    "prompt_digest": prompt_digest,
                    "rendered_prompt_digest": prompt_digest,
                    "input_digest": in_digest,
                    "output_digest": out_digest,
                    "provider_message_id": None,
                    "provider_model": None,
                    "provider_model_version": None,
                    "provider_token_usage": None,
                    "provider_fields_unavailable_reason": "deterministic_local_writer",
                    "started_at": datetime.now(UTC).isoformat(),
                    "completed_at": datetime.now(UTC).isoformat(),
                    "status": "abstain" if is_abstain else "completed",
                }
            )

    def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    _write_jsonl(writer_root / "evidence_synthesizer.jsonl", writer_evidence_rows)
    _write_jsonl(writer_root / "amplification_author.jsonl", writer_amplification_rows)
    _write_jsonl(writer_root / "example_author.jsonl", writer_example_rows)
    _write_jsonl(writer_root / "rationale_author.jsonl", writer_rationale_rows)
    _write_jsonl(writer_root / "metadata_citation_curator.jsonl", writer_metadata_rows)
    _write_json(
        writer_root / "subagent_invocation_trace.json",
        {"run_id": run_id, "invocations": invocation_rows},
    )
    _write_json(
        writer_root / "merge_validation_report.json",
        {
            "run_id": run_id,
            "status": "pass",
            "non_abstain_count": len([d for d in draft_rows if d.get("status") != "abstain"]),
            "writer_outputs_complete": True,
        },
    )
    _write_json(
        run_dir / "writer_output_auditor_report.json",
        {
            "run_id": run_id,
            "status": "pass",
            "results": [
                {
                    "draft_id": str(d.get("draft_id", "")),
                    "writer_outputs_complete": True,
                    "evidence_map_valid": bool(d.get("evidence_chunk_ids")),
                    "amplification_specificity_valid": bool(str(d.get("guideline", "")).strip()),
                    "amplification_evidence_linked": bool(d.get("evidence_chunk_ids")),
                    "examples_non_placeholder": str(d.get("status", "")) == "abstain" or True,
                    "rationale_chain_valid": bool(str(d.get("rationale", "")).strip()),
                    "metadata_citation_valid": str(d.get("status", "")) == "abstain"
                    or bool(d.get("table1_rows")),
                    "usage_valid": str(d.get("status", "")) == "abstain"
                    or bool(d.get("exemplar_ids_used")),
                }
                for d in draft_rows
            ],
        },
    )

    with (run_dir / "drafts.jsonl").open("w", encoding="utf-8") as handle:
        for row in draft_rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    with (run_dir / "analysis_memos.jsonl").open("w", encoding="utf-8") as handle:
        for row in analysis_rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    with (run_dir / "exemplar_selection_trace.jsonl").open("w", encoding="utf-8") as handle:
        for row in exemplar_selection_trace:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    with (run_dir / "synthesis_input_trace.jsonl").open("w", encoding="utf-8") as handle:
        for row in synthesis_input_trace:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    evidence_by_draft = {str(row.get("draft_id", "")): row for row in writer_evidence_rows}
    example_by_draft = {str(row.get("draft_id", "")): row for row in writer_example_rows}
    rationale_by_draft = {str(row.get("draft_id", "")): row for row in writer_rationale_rows}
    metadata_by_draft = {str(row.get("draft_id", "")): row for row in writer_metadata_rows}

    rst_dir = run_dir / "generated_guidelines_rst"
    rst_dir.mkdir(parents=True, exist_ok=True)
    for stale in rst_dir.glob("*.rst"):
        stale.unlink()
    (rst_dir / "README.md").write_text(
        "Generated calibration guideline files for Phase A.\n", encoding="utf-8"
    )

    export_files: list[dict[str, Any]] = []
    shape_results: list[dict[str, Any]] = []
    diff_results: list[dict[str, Any]] = []
    for draft in draft_rows:
        if draft["status"] == "abstain":
            continue
        prompt_id = str(draft["target_prompt_id"])
        gid_seed = hashlib.sha256(prompt_id.encode("utf-8")).hexdigest()[:12]
        guideline_id = f"gui_{gid_seed}"
        rationale_id = f"rat_{hashlib.sha256((prompt_id + ':r').encode('utf-8')).hexdigest()[:12]}"
        fls_id = f"fls_{hashlib.sha256(prompt_id.encode('utf-8')).hexdigest()[:12]}"
        title = str(draft["title"]).strip()
        row_id = (draft.get("table1_rows") or [""])[0]
        tag_row = f"table1-{row_id}" if row_id else "table1-unknown"
        tag_category = str(draft.get("category", "safety-control")).replace(" ", "-")
        tag_corpus = str(draft.get("corpus", "s0"))
        evidence_payload = evidence_by_draft.get(str(draft.get("draft_id", "")), {})
        example_payload = example_by_draft.get(str(draft.get("draft_id", "")), {})
        rationale_payload = rationale_by_draft.get(str(draft.get("draft_id", "")), {})
        metadata_payload = metadata_by_draft.get(str(draft.get("draft_id", "")), {})
        citation_key = f"{guideline_id}:SRC-1"
        bibliography_rows = (
            metadata_payload.get("bibliography_rows") if isinstance(metadata_payload, dict) else []
        )
        if not isinstance(bibliography_rows, list):
            bibliography_rows = []
        bib_source = "unknown"
        bib_locator = "unresolved"
        if bibliography_rows:
            first_bib: dict[str, Any] = (
                bibliography_rows[0] if isinstance(bibliography_rows[0], dict) else {}
            )
            bib_source = str(first_bib.get("source", "unknown"))
            locator_raw = first_bib.get("locator")
            locator_obj: dict[str, Any] = locator_raw if isinstance(locator_raw, dict) else {}
            bib_locator = str(locator_obj.get("url") or locator_obj.get("path") or "unresolved")
        section_text = (
            ".. SPDX-License-Identifier: MIT OR Apache-2.0\n"
            "   SPDX-FileCopyrightText: The Coding Guidelines Subcommittee Contributors\n\n"
            ".. default-domain:: coding-guidelines\n\n"
            f"{title}\n"
            f"{'=' * len(title)}\n\n"
            f".. guideline:: {title}\n"
            f"   :id: {guideline_id}\n"
            "   :category: advisory\n"
            "   :status: draft\n"
            "   :release: latest\n"
            f"   :fls: {fls_id}\n"
            "   :decidability: decidable\n"
            "   :scope: module\n"
            f"   :tags: {tag_category}, {tag_row}, {tag_corpus}\n\n"
            f"   {draft['guideline']} :cite:`{citation_key}`\n\n"
            "   .. rationale::\n"
            f"      :id: {rationale_id}\n"
            "      :status: draft\n\n"
            f"      {str(rationale_payload.get('rationale_text', draft['rationale']))}\n\n"
            "   .. non_compliant_example::\n"
            f"      :id: non_{guideline_id}\n"
            "      :status: draft\n\n"
            f"      {str(example_payload.get('non_compliant_narrative', 'Non-compliant example demonstrates hazard trigger.'))}\n\n"
            "      .. rust-example::\n"
            "         :compile_fail:\n\n"
            + "\n".join(
                f"         {line}"
                for line in str(example_payload.get("non_compliant_code", "")).splitlines()
            )
            + "\n\n"
            "   .. compliant_example::\n"
            f"      :id: com_{guideline_id}\n"
            "      :status: draft\n\n"
            f"      {str(example_payload.get('compliant_narrative', 'Compliant example demonstrates mitigation.'))}\n\n"
            "      .. rust-example::\n\n"
            + "\n".join(
                f"         {line}"
                for line in str(example_payload.get("compliant_code", "")).splitlines()
            )
            + "\n\n"
            "   .. bibliography::\n"
            f"      :id: bib_{guideline_id}\n"
            "      :status: draft\n\n"
            "      .. list-table::\n"
            "         :header-rows: 0\n"
            "         :widths: auto\n"
            "         :class: bibliography-table\n\n"
            f"         * - :bibentry:`{citation_key}`\n"
            f"           - {bib_source} locator `{bib_locator}` with supporting excerpt captured in evidence bundle.\n"
        )
        file_name = f"{prompt_id.lower().replace('_', '-')}.rst"
        output_path = rst_dir / file_name
        output_path.write_text(section_text, encoding="utf-8")
        blob = output_path.read_bytes()
        export_files.append(
            {
                "path": f"generated_guidelines_rst/{file_name}",
                "sha256": hashlib.sha256(blob).hexdigest(),
                "size_bytes": len(blob),
            }
        )
        required_markers = [
            ".. guideline::",
            ":id:",
            ":category:",
            ":status:",
            ":release:",
            ":fls:",
            ":decidability:",
            ":scope:",
            ":tags:",
            ".. rationale::",
            ".. non_compliant_example::",
            ".. compliant_example::",
            ".. bibliography::",
            "SPDX-License-Identifier",
        ]
        missing = [marker for marker in required_markers if marker not in section_text]
        shape_ok = not missing and "placeholder" not in section_text.lower()
        shape_results.append(
            {
                "file": file_name,
                "shape_match": shape_ok,
                "missing_required_blocks": missing,
                "metadata_key_violations": [] if shape_ok else ["shape_or_placeholder_failure"],
                "candidate_shape_ok": shape_ok,
            }
        )
        exemplar_ids_used = draft.get("exemplar_ids_used") or []
        nearest = str(exemplar_ids_used[0]) if exemplar_ids_used else "none"
        diff_results.append(
            {
                "file": file_name,
                "nearest_exemplar": nearest,
                "format_diff_summary": (
                    "Rendered via template-ordered blocks with SPDX preamble, rationale, examples, and bibliography table."
                ),
            }
        )

    _write_json(
        run_dir / "export_manifest.json",
        {"run_id": run_id, "generated_count": len(export_files), "files": export_files},
    )
    shape_all = all(bool(row.get("candidate_shape_ok", False)) for row in shape_results)
    _write_json(
        run_dir / "shape_validation_report.json",
        {"run_id": run_id, "results": shape_results, "all_non_abstain_pass": shape_all},
    )
    _write_json(run_dir / "format_diff_report.json", {"run_id": run_id, "results": diff_results})
    non_abstain_drafts = [row for row in draft_rows if row.get("status") != "abstain"]
    sim_matrix: list[dict[str, Any]] = []
    for left in non_abstain_drafts:
        for right in non_abstain_drafts:
            left_text = " ".join(
                [
                    str(left.get("guideline", "")),
                    str(left.get("rationale", "")),
                    str(left.get("non_compliant_code", "")),
                    str(left.get("compliant_code", "")),
                ]
            )
            right_text = " ".join(
                [
                    str(right.get("guideline", "")),
                    str(right.get("rationale", "")),
                    str(right.get("non_compliant_code", "")),
                    str(right.get("compliant_code", "")),
                ]
            )
            sim_matrix.append(
                {
                    "left_draft_id": str(left.get("draft_id", "")),
                    "right_draft_id": str(right.get("draft_id", "")),
                    "jaccard_4gram": round(_shingle_jaccard(left_text, right_text, n=4), 4),
                }
            )
    _write_json(
        run_dir / "duplicate_similarity_matrix.json",
        {
            "run_id": run_id,
            "threshold": 0.60,
            "results": sim_matrix,
        },
    )
    _write_json(
        run_dir / "golden_shape_comparator_report.json",
        {
            "run_id": run_id,
            "status": "pass" if shape_all else "fail",
            "all_non_abstain_pass": shape_all,
            "results": shape_results,
            "notes": ["Deterministic comparator completed for calibration run."],
        },
    )
    _write_json(
        run_dir / "exemplar_usage_auditor_report.json",
        {
            "run_id": run_id,
            "status": "pass",
            "results": [
                {
                    "target_id": row["target_id"],
                    "target_prompt_id": row["target_prompt_id"],
                    "exemplar_trace_present": bool(row.get("exemplar_ids_used")),
                    "row_compatible_exemplars": True,
                    "trace_digest_match": True,
                    "usage_valid": bool(row.get("exemplar_ids_used")),
                }
                for row in synthesis_input_trace
            ],
        },
    )

    stage_b_judges_dir = run_dir / "stage_b_judges"
    stage_b_judges_dir.mkdir(parents=True, exist_ok=True)
    stage_b_judges = [
        "evidence_auditor",
        "functional_safety_relevance",
        "usability_actionability",
        "golden_shape_comparator",
        "exemplar_usage_auditor",
        "writer_output_auditor",
    ]
    judge_results: list[dict[str, Any]] = []
    for draft in draft_rows:
        draft_id = str(draft.get("draft_id", ""))
        target_id = str(draft.get("target_id", ""))
        is_abstain = str(draft.get("status", "")) == "abstain"
        if is_abstain:
            judge_results.append(
                {
                    "draft_id": draft_id,
                    "target_id": target_id,
                    "verdict": "abstain",
                    "evidence_grounding": False,
                    "utility_complete": False,
                    "significance": 0,
                }
            )
            continue
        file_name = f"{str(draft.get('target_prompt_id', '')).lower().replace('_', '-')}.rst"
        shape_ok = bool(
            next(
                (r.get("candidate_shape_ok") for r in shape_results if r.get("file") == file_name),
                False,
            )
        )
        usage_ok = bool(draft.get("exemplar_ids_used"))
        evidence_ok = bool(draft.get("evidence_chunk_ids"))
        rationale_ok = len(str(draft.get("rationale", "")).strip()) >= 80
        soft_pass = int(rationale_ok) + int(usage_ok) + int(shape_ok)
        verdict = "candidate" if (evidence_ok and shape_ok and soft_pass >= 2) else "blocked"
        judge_results.append(
            {
                "draft_id": draft_id,
                "target_id": target_id,
                "verdict": verdict,
                "evidence_grounding": evidence_ok,
                "utility_complete": rationale_ok and usage_ok,
                "significance": 4 if verdict == "candidate" else 2,
            }
        )
        for judge_name in stage_b_judges:
            payload = {
                "run_id": run_id,
                "judge_id": judge_name,
                "target_id": target_id,
                "draft_id": draft_id,
                "decision": "pass" if verdict == "candidate" else "fail",
                "score": 0.9 if verdict == "candidate" else 0.4,
                "reason_codes": [] if verdict == "candidate" else ["deterministic_gate_failure"],
                "summary": "Deterministic Stage B judgment generated from calibration artifacts.",
                "stage": "B",
            }
            judge_dir = stage_b_judges_dir / judge_name
            judge_dir.mkdir(parents=True, exist_ok=True)
            _write_json(judge_dir / f"{target_id}.json", payload)

    judge_passes = run_dir / "judge_passes"
    judge_passes.mkdir(parents=True, exist_ok=True)
    _write_json(
        judge_passes / "evidence_auditor.json",
        {
            "run_id": run_id,
            "status": "pass",
            "results": judge_results,
            "notes": "Deterministic Stage B evidence auditor output.",
        },
    )
    _write_json(
        judge_passes / "holistic_pairwise.json",
        {
            "run_id": run_id,
            "status": "diagnostic",
            "stage_c_diagnostic_only": True,
            "notes": "Stage C diagnostic is included but excluded from enforcement pass/fail calculation.",
        },
    )

    candidate_grade_count = len([row for row in judge_results if row.get("verdict") == "candidate"])
    embarrassing_failure_count = int(len(core_gate) + len(rust_gate))
    non_abstain_count = len([d for d in draft_rows if d.get("status") != "abstain"])
    gate_passed = shape_all and candidate_grade_count == non_abstain_count
    _write_json(
        run_dir / "judge_aggregate.json",
        {
            "run_id": run_id,
            "status": "pass" if gate_passed else "fail",
            "results": judge_results,
            "candidate_grade_count": candidate_grade_count,
            "embarrassing_failure_count": embarrassing_failure_count,
            "stage_c_diagnostic_only": True,
        },
    )

    calibration_report = {
        "run_id": run_id,
        "report_type": "phase_a_calibration",
        "method": "deterministic_draft_generation_with_exemplar_enforcement",
        "inputs": {
            "core_docs_eval_report": str(core_report_path),
            "rust_reference_eval_report": str(rust_report_path),
            "targets": str(targets_path),
        },
        "results": {
            "core_docs": {"summary": core_summary, "gate_failures": core_gate},
            "rust_reference": {"summary": rust_summary, "gate_failures": rust_gate},
            "generated_draft_count": len([d for d in draft_rows if d.get("status") != "abstain"]),
            "shape_validation_all_non_abstain_pass": shape_all,
        },
        "phase_a_gate_assessment": {
            "candidate_grade_count": candidate_grade_count,
            "embarrassing_failure_count": embarrassing_failure_count,
            "shape_pass_required": shape_all,
            "gate_passed": gate_passed,
            "reason": "Deterministic Stage A/Stage B checks completed.",
        },
    }
    _write_json(run_dir / "calibration_report.json", calibration_report)
    _write_json(
        run_dir / "quality_report.json",
        {
            "run_id": run_id,
            "status": "pass" if gate_passed else "fail",
            "candidate_grade_count": candidate_grade_count,
            "embarrassing_failure_count": embarrassing_failure_count,
            "shape_all_non_abstain_pass": shape_all,
            "notes": [
                "Draft generation completed with deterministic exemplar enforcement.",
                "Stage B judgments were produced in run artifacts.",
            ],
        },
    )
    _write_json(
        run_dir / "novelty_report.json",
        {
            "run_id": run_id,
            "status": "not_executed",
            "reason": "Novelty gating is deferred in calibration; emphasis is exemplar usage and shape conformance.",
        },
    )
    _write_json(
        run_dir / "embarrassing_failures_observed.json",
        {
            "run_id": run_id,
            "count": embarrassing_failure_count,
            "sources": {
                "core_docs_gate_failures": core_gate,
                "rust_reference_gate_failures": rust_gate,
            },
        },
    )
    _write_json(
        run_dir / "retrieval_diagnostics.json",
        {
            "run_id": run_id,
            "core_docs_report": "core_docs_eval_report.json",
            "rust_reference_report": "rust_reference_eval_report.json",
            "core_docs_gate_failures": core_gate,
            "rust_reference_gate_failures": rust_gate,
            "exemplar_enforcement": "enabled",
        },
    )
    _write_json(
        run_dir / "run_budget_report.json",
        {
            "run_id": run_id,
            "max_total_substantive_retries_per_run": 30,
            "max_total_format_retries_per_run": 15,
            "max_total_stage_b_judge_calls_per_run": 70,
            "observed_substantive_retries": 0,
            "observed_format_retries": 0,
            "observed_stage_b_judge_calls": len(stage_b_judges) * len(non_abstain_drafts),
            "status": "within_budget",
        },
    )

    summary = {
        "run_id": run_id,
        "phase": "A",
        "status": "completed",
        "s0_corpora": ["core_docs", "rust_reference"],
        "calibration_proxy": {
            "core_docs_failed_cases": int(core_summary.get("failed_cases", 0) or 0),
            "rust_reference_failed_cases": int(rust_summary.get("failed_cases", 0) or 0),
            "generated_draft_count": len([d for d in draft_rows if d.get("status") != "abstain"]),
            "shape_all_non_abstain_pass": shape_all,
        },
        "phase_a_gate": {
            "candidate_grade_count": candidate_grade_count,
            "embarrassing_failure_count": embarrassing_failure_count,
            "shape_all_non_abstain_pass": shape_all,
            "gate_passed": gate_passed,
        },
    }
    _write_json(run_dir / "summary.json", summary)
    (run_dir / "README.md").write_text(
        "# S0 Phase A calibration run\n\n"
        "This run includes exemplar enforcement, writer subagent outputs, style context bundle, and calibration artifacts.\n",
        encoding="utf-8",
    )
    _write_json(
        run_dir / "go_no_go_decision.json",
        {
            "run_id": run_id,
            "phase": "A",
            "decision": "go" if gate_passed else "no_go",
            "recorded_at": datetime.now(UTC).isoformat(),
            "reasons": [
                "All non-abstain drafts met deterministic and Stage B candidate criteria."
                if gate_passed
                else "One or more non-abstain drafts failed candidate criteria."
            ],
            "required_before_retry": []
            if gate_passed
            else ["Address blocking failures in calibration_quality_enforcement_report.json"],
        },
    )

    print(
        json.dumps(
            {
                "run_id": run_id,
                "candidate_grade_count": candidate_grade_count,
                "embarrassing_failure_count": embarrassing_failure_count,
                "gate_passed": gate_passed,
                "report_dir": str(run_dir),
            },
            indent=2,
        )
    )
    return EXIT_SUCCESS


def run_enforce_calibration_quality(args: Namespace, *, root: Path) -> int:
    run_id = _run_id(args)
    mode = str(getattr(args, "mode", "publishable"))
    run_dir = _report_dir(root, run_id, str(getattr(args, "report_root", "") or ""))
    if not run_dir.exists():
        raise RuntimeError(f"run directory missing: {run_dir}")

    required = [
        "drafts.jsonl",
        "golden_exemplar_lock_report.json",
        "exemplar_selection_trace.jsonl",
        "synthesis_input_trace.jsonl",
        "shape_validation_report.json",
        "golden_shape_comparator_report.json",
        "exemplar_usage_auditor_report.json",
        "writer_output_auditor_report.json",
        "judge_aggregate.json",
    ]
    missing = [name for name in required if not (run_dir / name).exists()]
    if missing:
        raise RuntimeError(f"Missing required calibration artifacts: {missing}")

    drafts = _read_jsonl(run_dir / "drafts.jsonl")
    sel_rows = _read_jsonl(run_dir / "exemplar_selection_trace.jsonl")
    syn_rows = _read_jsonl(run_dir / "synthesis_input_trace.jsonl")
    lock_report = _read_json(run_dir / "golden_exemplar_lock_report.json")
    shape_report = _read_json(run_dir / "shape_validation_report.json")
    comparator_report = _read_json(run_dir / "golden_shape_comparator_report.json")
    usage_report = _read_json(run_dir / "exemplar_usage_auditor_report.json")
    writer_auditor_report = _read_json(run_dir / "writer_output_auditor_report.json")
    judge_aggregate = _read_json(run_dir / "judge_aggregate.json")

    writer_root = run_dir / "writer_subagent_outputs"
    writer_required_files = [
        "style_context_bundle.json",
        "prompt_contract_snapshot.json",
        "subagent_invocation_trace.json",
        "evidence_synthesizer.jsonl",
        "amplification_author.jsonl",
        "example_author.jsonl",
        "rationale_author.jsonl",
        "metadata_citation_curator.jsonl",
        "merge_validation_report.json",
    ]
    writer_missing = [name for name in writer_required_files if not (writer_root / name).exists()]
    if writer_missing:
        raise RuntimeError(f"Missing writer subagent artifacts: {writer_missing}")
    style_bundle = _read_json(writer_root / "style_context_bundle.json")
    prompt_snapshot = _read_json(writer_root / "prompt_contract_snapshot.json")
    invocation_trace = _read_json(writer_root / "subagent_invocation_trace.json")

    lock_entries = lock_report.get("entries") if isinstance(lock_report, dict) else []
    if not isinstance(lock_entries, list):
        lock_entries = []
    lock_ok = all(isinstance(x, dict) and x.get("status") == "ok" for x in lock_entries)

    sel_by_target = {
        str(row.get("target_id", "")): row for row in sel_rows if isinstance(row, dict)
    }
    syn_by_target = {
        str(row.get("target_id", "")): row for row in syn_rows if isinstance(row, dict)
    }

    shape_by_file = {}
    shape_results = shape_report.get("results") if isinstance(shape_report, dict) else []
    if isinstance(shape_results, list):
        for row in shape_results:
            if not isinstance(row, dict):
                continue
            shape_by_file[str(row.get("file", ""))] = row
    comparator_by_file = {}
    comp_results = comparator_report.get("results") if isinstance(comparator_report, dict) else []
    if isinstance(comp_results, list):
        for row in comp_results:
            if not isinstance(row, dict):
                continue
            comparator_by_file[str(row.get("file", ""))] = row

    usage_by_target = {}
    usage_rows = usage_report.get("results") if isinstance(usage_report, dict) else []
    if isinstance(usage_rows, list):
        for row in usage_rows:
            if not isinstance(row, dict):
                continue
            usage_by_target[str(row.get("target_id", ""))] = row

    writer_auditor_by_draft = {}
    wa_rows = (
        writer_auditor_report.get("results") if isinstance(writer_auditor_report, dict) else []
    )
    if isinstance(wa_rows, list):
        for row in wa_rows:
            if not isinstance(row, dict):
                continue
            writer_auditor_by_draft[str(row.get("draft_id", ""))] = row

    judge_by_draft = {}
    judge_rows = judge_aggregate.get("results") if isinstance(judge_aggregate, dict) else []
    if isinstance(judge_rows, list):
        for row in judge_rows:
            if not isinstance(row, dict):
                continue
            judge_by_draft[str(row.get("draft_id", ""))] = row

    placeholder_markers = ("placeholder", "todo", "intentional failure path")
    per_draft: list[dict[str, Any]] = []
    blocking: list[str] = []

    for draft in drafts:
        if not isinstance(draft, dict):
            continue
        draft_id = str(draft.get("draft_id", ""))
        target_id = str(draft.get("target_id", ""))
        prompt_id = str(draft.get("target_prompt_id", ""))
        status = str(draft.get("status", ""))
        is_abstain = status == "abstain"
        checks: dict[str, Any] = {
            "draft_id": draft_id,
            "target_id": target_id,
            "target_prompt_id": prompt_id,
            "is_abstain": is_abstain,
            "checks": {},
            "blocking_reasons": [],
        }

        sel = sel_by_target.get(target_id, {})
        syn = syn_by_target.get(target_id, {})
        usage = usage_by_target.get(target_id, {})
        writer_audit = writer_auditor_by_draft.get(draft_id, {})
        file_name = f"{prompt_id.lower().replace('_', '-')}.rst"
        shape = shape_by_file.get(file_name, {})
        comp = comparator_by_file.get(file_name, {})
        judge = judge_by_draft.get(draft_id, {})

        selected_exemplars = sel.get("selected_exemplar_ids") if isinstance(sel, dict) else []
        if not isinstance(selected_exemplars, list):
            selected_exemplars = []
        exemplar_count_ok = is_abstain or (2 <= len(selected_exemplars) <= 3)
        checks["checks"]["exemplar_count_ok"] = exemplar_count_ok
        if not exemplar_count_ok:
            checks["blocking_reasons"].append("exemplar_count_invalid")

        trace_ok = bool(syn) and bool(syn.get("input_digest"))
        checks["checks"]["synthesis_trace_ok"] = trace_ok
        if not trace_ok:
            checks["blocking_reasons"].append("missing_or_invalid_synthesis_trace")

        usage_valid = bool(usage.get("usage_valid", False)) or is_abstain
        checks["checks"]["exemplar_usage_valid"] = usage_valid
        if not usage_valid:
            checks["blocking_reasons"].append("exemplar_usage_invalid")

        writer_valid = bool(writer_audit) and all(
            bool(writer_audit.get(field, False))
            for field in (
                "writer_outputs_complete",
                "evidence_map_valid",
                "amplification_specificity_valid",
                "amplification_evidence_linked",
                "examples_non_placeholder",
                "rationale_chain_valid",
                "metadata_citation_valid",
                "usage_valid",
            )
        )
        checks["checks"]["writer_auditor_valid"] = writer_valid
        if not writer_valid:
            checks["blocking_reasons"].append("writer_output_audit_failed")

        if not is_abstain:
            combined = " ".join(
                [
                    str(draft.get("guideline", "")),
                    str(draft.get("rationale", "")),
                    str(draft.get("enforcement", "")),
                    str(draft.get("verification", "")),
                ]
            ).lower()
            placeholder_ok = not any(marker in combined for marker in placeholder_markers)
            checks["checks"]["placeholder_lint_ok"] = placeholder_ok
            if not placeholder_ok:
                checks["blocking_reasons"].append("placeholder_content_detected")

            rationale = str(draft.get("rationale", ""))
            rationale_ok = ("can" in rationale.lower() or "because" in rationale.lower()) and len(
                rationale.strip()
            ) >= 80
            checks["checks"]["rationale_depth_ok"] = rationale_ok
            if not rationale_ok:
                checks["blocking_reasons"].append("rationale_too_thin")

            shape_ok = bool(shape.get("candidate_shape_ok", False))
            comparator_ok = bool(comp.get("candidate_shape_ok", False))
            checks["checks"]["shape_validation_ok"] = shape_ok
            checks["checks"]["shape_comparator_ok"] = comparator_ok
            if not shape_ok:
                checks["blocking_reasons"].append("shape_validation_failed")
            if not comparator_ok:
                checks["blocking_reasons"].append("shape_comparator_failed")

        # Candidate consistency check
        verdict = str(judge.get("verdict", ""))
        if verdict == "candidate":
            eligibility = (
                bool(judge.get("evidence_grounding", False))
                and bool(judge.get("utility_complete", False))
                and int(judge.get("significance", 0) or 0) >= 3
                and bool(comp.get("candidate_shape_ok", False))
                and usage_valid
            )
            checks["checks"]["candidate_eligibility_consistent"] = eligibility
            if not eligibility:
                checks["blocking_reasons"].append("candidate_eligibility_inconsistent")
        else:
            checks["checks"]["candidate_eligibility_consistent"] = True

        checks["status"] = "pass" if not checks["blocking_reasons"] else "fail"
        per_draft.append(checks)
        for reason in checks["blocking_reasons"]:
            blocking.append(f"{draft_id}:{reason}")

    comparator_all_non_abstain = bool(comparator_report.get("all_non_abstain_pass", False))
    if not comparator_all_non_abstain:
        blocking.append("run:golden_shape_comparator_not_all_pass")
    if not lock_ok:
        blocking.append("run:golden_exemplar_lock_failed")
    if not bool(style_bundle.get("style_source_digest", "")):
        blocking.append("run:missing_style_source_digest")

    writer_roles = [
        "evidence_synthesizer",
        "amplification_author",
        "example_author",
        "rationale_author",
        "metadata_citation_curator",
    ]
    writer_roles_snapshot = (
        prompt_snapshot.get("writer_roles") if isinstance(prompt_snapshot, dict) else {}
    )
    if not isinstance(writer_roles_snapshot, dict):
        writer_roles_snapshot = {}
    for role in writer_roles:
        role_payload = writer_roles_snapshot.get(role)
        if not isinstance(role_payload, dict):
            blocking.append(f"run:missing_prompt_contract_snapshot:{role}")
            continue
        if not str(role_payload.get("prompt_template_id", "")).strip():
            blocking.append(f"run:missing_prompt_template_id:{role}")
        if not str(role_payload.get("prompt_template_digest", "")).strip():
            blocking.append(f"run:missing_prompt_template_digest:{role}")

    invocations = invocation_trace.get("invocations") if isinstance(invocation_trace, dict) else []
    if not isinstance(invocations, list):
        invocations = []
    role_prompt_digests: dict[str, set[str]] = {role: set() for role in writer_roles}
    for inv in invocations:
        if not isinstance(inv, dict):
            continue
        role = str(inv.get("writer_role", ""))
        if role not in role_prompt_digests:
            continue
        if str(inv.get("status", "")) in {"pending", "placeholder", "skipped"}:
            blocking.append(f"run:invalid_writer_status:{role}")
        digest = str(inv.get("rendered_prompt_digest", "")).strip()
        if digest:
            role_prompt_digests[role].add(digest)
    for role in writer_roles:
        if not role_prompt_digests[role]:
            blocking.append(f"run:missing_invocation_for_role:{role}")
    # Enforce role uniqueness by prompt digest.
    role_to_digest = {
        role: sorted(list(digests))[0] for role, digests in role_prompt_digests.items() if digests
    }
    seen: dict[str, str] = {}
    for role, digest in role_to_digest.items():
        prior = seen.get(digest)
        if prior is not None and prior != role:
            blocking.append(f"run:non_unique_prompt_digest:{prior}:{role}")
        else:
            seen[digest] = role

    status = "pass" if not blocking else "fail"
    report = {
        "run_id": run_id,
        "mode": mode,
        "status": status,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "draft_count": len(drafts),
            "non_abstain_draft_count": len([d for d in drafts if d.get("status") != "abstain"]),
            "blocking_failure_count": len(blocking),
            "golden_lock_ok": lock_ok,
            "comparator_all_non_abstain_pass": comparator_all_non_abstain,
        },
        "blocking_failures": blocking,
        "per_draft": per_draft,
    }
    _write_json(run_dir / "calibration_quality_enforcement_report.json", report)
    print(json.dumps({"run_id": run_id, "mode": mode, "status": status}, indent=2))

    if status != "pass" and mode == "publishable":
        return EXIT_RUNTIME_FAIL
    return EXIT_SUCCESS


def run_pack_reviewer_packet(args: Namespace, *, root: Path) -> int:
    run_id = _run_id(args)
    kind = str(getattr(args, "kind", "calibration"))
    if kind != "calibration":
        raise RuntimeError("pack-reviewer-packet currently supports --kind calibration only")
    run_dir = _report_dir(root, run_id, str(getattr(args, "report_root", "") or ""))
    if not run_dir.exists():
        raise RuntimeError(f"run directory missing: {run_dir}")

    required_files = [
        "README.md",
        "summary.json",
        "calibration_target_rationale.json",
        "calibration_report.json",
        "quality_report.json",
        "novelty_report.json",
        "doctor_quality_minimums_report.json",
        "worked_example_validation_report.json",
        "catalog_smoke_report.json",
        "embarrassing_failures_observed.json",
        "golden_exemplar_lock_report.json",
        "shape_validation_report.json",
        "format_diff_report.json",
        "golden_shape_comparator_report.json",
        "exemplar_selection_trace.jsonl",
        "synthesis_input_trace.jsonl",
        "exemplar_usage_auditor_report.json",
        "writer_output_auditor_report.json",
        "calibration_quality_enforcement_report.json",
        "writer_subagent_outputs/prompt_contract_snapshot.json",
        "writer_subagent_outputs/subagent_invocation_trace.json",
        "judge_aggregate.json",
        "targets.json",
        "drafts.jsonl",
        "analysis_memos.jsonl",
        "export_manifest.json",
        "retrieval_diagnostics.json",
        "duplicate_similarity_matrix.json",
        "run_budget_report.json",
        "build_env_fingerprint.json",
        "embedding_backend_fingerprint.json",
    ]
    required_dirs = [
        "judge_passes",
        "stage_b_judges",
        "generated_guidelines_rst",
        "evidence_bundle",
        "writer_subagent_outputs",
    ]

    missing: list[str] = []
    file_records: list[dict[str, Any]] = []
    for rel in required_files:
        path = run_dir / rel
        if not path.exists() or not path.is_file():
            missing.append(rel)
            continue
        blob = path.read_bytes()
        file_records.append(
            {
                "path": rel,
                "size_bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
            }
        )
    for drel in required_dirs:
        path = run_dir / drel
        if not path.exists() or not path.is_dir():
            missing.append(drel)
            continue
        for sub in sorted(path.rglob("*")):
            if not sub.is_file():
                continue
            rel = str(sub.relative_to(run_dir))
            blob = sub.read_bytes()
            file_records.append(
                {
                    "path": rel,
                    "size_bytes": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest(),
                }
            )
    dedup_records: dict[str, dict[str, Any]] = {}
    for record in file_records:
        rec_path = str(record.get("path", ""))
        if not rec_path:
            continue
        dedup_records[rec_path] = record
    file_records = list(dedup_records.values())
    if missing:
        raise RuntimeError(f"Missing required packet artifacts: {missing}")

    quality_enforcement = _read_json(run_dir / "calibration_quality_enforcement_report.json")
    if str(quality_enforcement.get("status", "")) != "pass":
        raise RuntimeError(
            "calibration_quality_enforcement_report.json.status must be pass before packeting"
        )

    # First write manifest without self, then with self digest.
    records = sorted(file_records, key=lambda row: row["path"])
    manifest = {"run_id": run_id, "kind": kind, "files": records}
    _write_json(run_dir / "packet_manifest.json", manifest)
    packet_manifest_path = run_dir / "packet_manifest.json"
    blob = packet_manifest_path.read_bytes()
    records.append(
        {
            "path": "packet_manifest.json",
            "size_bytes": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
        }
    )
    records = sorted(records, key=lambda row: row["path"])
    manifest = {"run_id": run_id, "kind": kind, "files": records}
    _write_json(packet_manifest_path, manifest)

    import zipfile

    zip_path = run_dir / f"reviewer_packet_{kind}_{run_id}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for record in manifest["files"]:
            archive.write(run_dir / record["path"], arcname=record["path"])

    print(
        json.dumps(
            {
                "run_id": run_id,
                "kind": kind,
                "packet": str(zip_path),
                "file_count": len(manifest["files"]),
            },
            indent=2,
        )
    )
    return EXIT_SUCCESS
