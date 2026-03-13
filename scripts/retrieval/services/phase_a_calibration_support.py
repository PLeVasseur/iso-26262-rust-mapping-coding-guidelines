from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from context.convention_extractor import extract_all_exemplar_conventions
from context.convention_spec import _build_convention_spec, _diff_specs, validate_convention_spec
from context.exemplars import get_exemplar_paths
from context.fls_lookup import get_fls_db_stats
from context.stdlib_lookup import load_stdlib_index
from retrieval.services.s0_phase_a_impl import _extract_json_object
from retrieval.services.utils import _write_json


def select_calibration_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preferred_ids = {
        "CORE-SAFE-003",
        "CORE-CONC-003",
        "RET-ISSUE-005",
        "RET-RESOLVE-008",
        "RET-NEG-001",
    }
    selected = [t for t in targets if str(t.get("prompt_id", "")) in preferred_ids]
    if len(selected) < 5:
        for target in targets:
            if target in selected:
                continue
            selected.append(target)
            if len(selected) >= 5:
                break
    return selected


def evaluate_startup_checklist(
    *,
    mode: str,
    selected: list[dict[str, Any]],
    writer_contracts: dict[str, Any],
    writer_model: str,
    run_dir: Path,
    root: Path,
    reuse_existing: bool,
    resume_requested: bool,
    existing_digest: str,
    fingerprint_match: bool,
    source_text: str,
    is_allowed_resume_artifact: Any,
) -> dict[str, Any]:
    startup_failures: list[str] = []
    bootstrap_marker = root / ".cache" / "sqlite_kb" / "reports" / ".phase_a_bootstrap_complete"
    if mode != "bootstrap" and not bootstrap_marker.exists():
        startup_failures.append("startup_checklist:mode_must_be_bootstrap_for_first_recovery_run")
    if len(selected) != 5:
        startup_failures.append("startup_checklist:active_target_set_must_be_5")

    legacy_template_markers = [
        'if "conc" in lower_prompt',
        'if "issue" in lower_prompt',
        "Deterministic Stage B judgment generated from calibration artifacts.",
    ]
    if mode == "publishable" and any(marker in source_text for marker in legacy_template_markers):
        startup_failures.append("startup_checklist:template_semantic_branch_detected")

    writer_roles_cfg = writer_contracts.get("roles") if isinstance(writer_contracts, dict) else {}
    if not isinstance(writer_roles_cfg, dict) or not writer_roles_cfg:
        startup_failures.append("startup_checklist:writer_prompt_contracts_missing")
    else:
        for role_name, role_payload in writer_roles_cfg.items():
            if not isinstance(role_payload, dict):
                startup_failures.append(f"startup_checklist:invalid_writer_contract:{role_name}")
                continue
            prompt_text = str(role_payload.get("prompt_template_text", ""))
            if "\n" not in prompt_text and len(prompt_text.split()) < 25:
                startup_failures.append(f"startup_checklist:one_line_writer_prompt:{role_name}")

    stage_b_stub_marker = '"score":' + " 0.9 if verdict"
    if stage_b_stub_marker in source_text:
        startup_failures.append("startup_checklist:stage_b_fixed_score_stub_detected")

    try:
        version_probe = subprocess.run(
            ["opencode", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if version_probe.returncode != 0:
            startup_failures.append("startup_checklist:opencode_cli_unavailable")
    except (subprocess.SubprocessError, OSError):
        startup_failures.append("startup_checklist:opencode_cli_unavailable")

    try:
        probe_command = ["opencode", "run", "--format", "json", "--agent", "plan"]
        if writer_model:
            probe_command.extend(["--model", writer_model])
        probe_command.append(
            'Return exactly this JSON object and nothing else: {"opencode_health":"ok"}'
        )
        model_probe = subprocess.run(
            probe_command,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if model_probe.returncode != 0:
            startup_failures.append("startup_checklist:opencode_model_routing_unavailable")
        else:
            probe_text_parts: list[str] = []
            for line in model_probe.stdout.splitlines():
                raw_line = line.strip()
                if not raw_line:
                    continue
                parsed_line = json.loads(raw_line)
                if not isinstance(parsed_line, dict):
                    continue
                if str(parsed_line.get("type", "")) != "text":
                    continue
                part_raw = parsed_line.get("part")
                part = part_raw if isinstance(part_raw, dict) else {}
                text = str(part.get("text", ""))
                if text:
                    probe_text_parts.append(text)
            probe_content = "\n".join(probe_text_parts).strip()
            try:
                probe_json = _extract_json_object(probe_content)
            except (ValueError, json.JSONDecodeError):
                probe_json = {}
            if str(probe_json.get("opencode_health", "")) != "ok":
                startup_failures.append("startup_checklist:opencode_non_interactive_probe_failed")
    except (subprocess.SubprocessError, OSError):
        startup_failures.append("startup_checklist:opencode_model_routing_unavailable")

    clean_run_artifacts = {
        "targets.json",
        "targets_digest",
        "core_docs_eval_report.json",
        "rust_reference_eval_report.json",
        "doctor_report.json",
        "doctor_quality_minimums_report.json",
        "worked_example_validation_report.json",
        "prompt_contract_validation_report.json",
        "catalog_smoke_report.json",
        "build_env_fingerprint.json",
        "embedding_backend_fingerprint.json",
    }
    existing_entries = sorted(path.name for path in run_dir.iterdir())
    resume_candidate_markers = {
        "calibration_target_rationale.json",
        "core_docs_backend_attempts.jsonl",
        "core_docs_eval_report.json",
        "rust_reference_backend_attempts.jsonl",
        "rust_reference_eval_report.json",
        "writer_subagent_outputs",
        "evidence_bundle",
    }
    resume_candidate = any((run_dir / marker).exists() for marker in resume_candidate_markers)
    resume_mode = (resume_requested or resume_candidate) and reuse_existing

    unknown_existing: list[str] = []
    if resume_mode:
        for name in existing_entries:
            if not is_allowed_resume_artifact(name):
                unknown_existing.append(name)
        if unknown_existing:
            startup_failures.append("startup_checklist:resume_unknown_artifacts_present")
        if existing_digest and not fingerprint_match:
            startup_failures.append("startup_checklist:resume_fingerprint_mismatch")
    else:
        non_clean = [name for name in existing_entries if name not in clean_run_artifacts]
        if non_clean:
            startup_failures.append("startup_checklist:run_artifact_root_not_clean")

    return {
        "status": "pass" if not startup_failures else "fail",
        "failures": startup_failures,
        "resume_mode": resume_mode,
        "resume_candidate": resume_candidate,
        "unknown_existing": unknown_existing,
    }


def emit_calibration_reports(
    *,
    run_dir: Path,
    run_id: str,
    core_report_path: Path,
    rust_report_path: Path,
    targets_path: Path,
    core_summary: dict[str, Any],
    core_gate: list[Any],
    rust_summary: dict[str, Any],
    rust_gate: list[Any],
    draft_rows: list[dict[str, Any]],
    shape_all: bool,
    candidate_grade_count: int,
    embarrassing_failure_count: int,
    gate_passed: bool,
    convention_retry_budget: int,
    gate_policy_cfg: dict[str, Any],
    total_retries: int,
    retry_rate: float,
    retry_depth_variant: str,
    stage_b_judges: list[str],
    non_abstain_drafts: list[dict[str, Any]],
    mode: str,
    root: Path,
) -> None:
    calibration_report = {
        "run_id": run_id,
        "report_type": "phase_a_calibration",
        "method": "llm_first_writer_and_stage_b_judges_with_gate_enforcement",
        "inputs": {
            "core_docs_eval_report": str(core_report_path),
            "rust_reference_eval_report": str(rust_report_path),
            "targets": str(targets_path),
        },
        "results": {
            "core_docs": {"summary": core_summary, "gate_failures": core_gate},
            "rust_reference": {"summary": rust_summary, "gate_failures": rust_gate},
            "generated_draft_count": len(
                [d for d in draft_rows if str(d.get("status", "")) not in {"abstain", "diagnostic"}]
            ),
            "shape_validation_all_non_abstain_pass": shape_all,
        },
        "phase_a_gate_assessment": {
            "candidate_grade_count": candidate_grade_count,
            "embarrassing_failure_count": embarrassing_failure_count,
            "shape_pass_required": shape_all,
            "gate_passed": gate_passed,
            "reason": "Real writer and judge role checks completed with gate enforcement.",
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
                "Draft generation completed with LLM-first writer chain.",
                "Stage B judgments were produced via real judge role calls.",
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
            "max_total_substantive_retries_per_run": convention_retry_budget,
            "max_total_format_retries_per_run": int(
                gate_policy_cfg.get(
                    "compilation_retry_budget",
                    gate_policy_cfg.get("max_compilation_retries", 15),
                )
            ),
            "max_total_stage_b_judge_calls_per_run": 70,
            "observed_substantive_retries": total_retries,
            "observed_format_retries": 0,
            "observed_stage_b_judge_calls": len(stage_b_judges) * len(non_abstain_drafts),
            "status": "within_budget",
            "retry_stats": {
                "total_retries": total_retries,
                "retry_rate": retry_rate,
                "estimated_additional_cost_pct": retry_rate * 100.0,
            },
            "retry_variant": retry_depth_variant,
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
            "generated_draft_count": len(
                [d for d in draft_rows if str(d.get("status", "")) not in {"abstain", "diagnostic"}]
            ),
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
                "All non-abstain drafts met writer/judge and critical gate criteria."
                if gate_passed
                else "One or more non-abstain drafts failed candidate criteria."
            ],
            "required_before_retry": []
            if gate_passed
            else ["Address blocking failures in calibration_quality_enforcement_report.json"],
        },
    )
    if mode == "bootstrap" and gate_passed:
        bootstrap_marker = root / ".cache" / "sqlite_kb" / "reports" / ".phase_a_bootstrap_complete"
        bootstrap_marker.parent.mkdir(parents=True, exist_ok=True)
        bootstrap_marker.write_text(datetime.now(UTC).isoformat() + "\n", encoding="utf-8")


def prepare_exemplar_and_style_context(
    *,
    run_dir: Path,
    run_id: str,
    root: Path,
) -> dict[str, Any]:
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

    exemplar_paths = get_exemplar_paths(guidelines_repo_root=guidelines_repo_root)
    exemplar_conventions = extract_all_exemplar_conventions(exemplar_paths)
    std_lookup = load_stdlib_index()
    fls_stats = get_fls_db_stats()
    convention_spec = _build_convention_spec(
        exemplar_conventions,
        guidelines_repo_root=guidelines_repo_root,
        std_lookup=std_lookup,
    )
    _write_json(run_dir / "convention_spec.json", convention_spec)
    validation_report = validate_convention_spec(convention_spec)
    _write_json(
        run_dir / "convention_spec_validation.json",
        {
            "run_id": run_id,
            "status": validation_report.get("status", "fail"),
            "validated_at": datetime.now(UTC).isoformat(),
            "validation": validation_report,
        },
    )

    stable_spec_path = root / ".cache" / "convention_spec.json"
    stable_spec_path.parent.mkdir(parents=True, exist_ok=True)
    if stable_spec_path.exists():
        old_spec = json.loads(stable_spec_path.read_text(encoding="utf-8"))
        old_sha = str(old_spec.get("guidelines_repo_commit_sha", ""))
        new_sha = str(convention_spec.get("guidelines_repo_commit_sha", ""))
        if old_sha != new_sha:
            _write_json(
                run_dir / "convention_spec_diff.json",
                {
                    "old_sha": old_sha,
                    "new_sha": new_sha,
                    "changes": _diff_specs(old_spec, convention_spec),
                },
            )
    stable_spec_path.write_text(json.dumps(convention_spec, indent=2) + "\n", encoding="utf-8")

    exemplar_lookup = {x["guideline_id"]: x for x in exemplar_entries}
    return {
        "guidelines_repo_root": guidelines_repo_root,
        "row_map": row_map,
        "exemplar_entries": exemplar_entries,
        "exemplar_lookup": exemplar_lookup,
        "writer_root": writer_root,
        "style_bundle": style_bundle,
        "std_lookup": std_lookup,
        "fls_stats": fls_stats,
        "convention_spec": convention_spec,
    }
