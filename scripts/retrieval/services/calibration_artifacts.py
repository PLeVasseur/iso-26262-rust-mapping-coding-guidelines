from __future__ import annotations

import hashlib
import json
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retrieval.services.utils import _read_json, _read_jsonl, _report_dir, _run_id, _write_json

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def run_enforce_calibration_quality(args: Namespace, *, root: Path) -> int:
    run_id = _run_id(args)
    mode = str(getattr(args, "mode", "publishable"))
    run_dir = _report_dir(root, run_id, str(getattr(args, "report_root", "") or ""))
    if not run_dir.exists():
        raise RuntimeError(f"run directory missing: {run_dir}")

    required = [
        "startup_checklist_report.json",
        "drafts.jsonl",
        "golden_exemplar_lock_report.json",
        "exemplar_selection_trace.jsonl",
        "synthesis_input_trace.jsonl",
        "shape_validation_report.json",
        "golden_shape_comparator_report.json",
        "exemplar_usage_auditor_report.json",
        "writer_output_auditor_report.json",
        "evidence_synthesizer_gate_report.json",
        "normalization_report.json",
        "citation_resolution_report.json",
        "duplicate_similarity_gate_report.json",
        "construct_evidence_alignment_report.json",
        "example_execution_semantics_report.json",
        "modality_category_consistency_report.json",
        "judge_aggregate.json",
        "convention_spec.json",
        "lookup_status.json",
        "convention_spec_validation.json",
        "fls_grounding_runtime_validation.json",
        "role_validation_report.json",
        "guideline_manifest.json",
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
    evidence_gate_report = _read_json(run_dir / "evidence_synthesizer_gate_report.json")
    normalization_report = _read_json(run_dir / "normalization_report.json")
    citation_resolution_report = _read_json(run_dir / "citation_resolution_report.json")
    duplicate_gate_report = _read_json(run_dir / "duplicate_similarity_gate_report.json")
    alignment_gate_report = _read_json(run_dir / "construct_evidence_alignment_report.json")
    example_semantics_report = _read_json(run_dir / "example_execution_semantics_report.json")
    modality_report = _read_json(run_dir / "modality_category_consistency_report.json")
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
        is_abstain = status in {"abstain", "diagnostic"}
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
    for gate_name, gate_payload in (
        ("evidence_synthesizer", evidence_gate_report),
        ("normalization", normalization_report),
        ("citation_resolution", citation_resolution_report),
        ("duplicate_similarity", duplicate_gate_report),
        ("construct_evidence_alignment", alignment_gate_report),
        ("example_execution_semantics", example_semantics_report),
        ("modality_category_consistency", modality_report),
    ):
        if str(gate_payload.get("status", "")) != "pass":
            blocking.append(f"run:{gate_name}_failed")

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
        for required_field in (
            "system_request_id",
            "request_started_at",
            "response_received_at",
            "prompt_digest",
            "response_digest",
            "transport_status",
            "transport_backend",
        ):
            if not str(inv.get(required_field, "")).strip():
                blocking.append(f"run:missing_invocation_field:{role}:{required_field}")
        if str(inv.get("transport_backend", "")).strip() != "opencode_cli":
            blocking.append(f"run:invalid_transport_backend:{role}")
        digest = str(inv.get("prompt_digest", "")).strip()
        if digest:
            role_prompt_digests[role].add(digest)
    for role in writer_roles:
        if not role_prompt_digests[role]:
            blocking.append(f"run:missing_invocation_for_role:{role}")

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
            "non_abstain_draft_count": len(
                [d for d in drafts if str(d.get("status", "")) not in {"abstain", "diagnostic"}]
            ),
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
        "startup_checklist_report.json",
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
        "duplicate_similarity_gate_report.json",
        "construct_evidence_alignment_report.json",
        "example_execution_semantics_report.json",
        "modality_category_consistency_report.json",
        "stage_b_judge_invocations.json",
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
