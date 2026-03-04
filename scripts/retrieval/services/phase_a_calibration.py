from __future__ import annotations

# Uses shared helpers/constants from s0_phase_a_impl during transition split.
from retrieval.services import s0_phase_a_impl as _impl

globals().update({name: getattr(_impl, name) for name in dir(_impl)})
from retrieval.services.phase_a_calibration_support import (
    emit_calibration_reports,
    evaluate_startup_checklist,
    prepare_exemplar_and_style_context,
    select_calibration_targets,
)
from retrieval.services.phase_a_writer_pipeline import execute_writer_validation_pipeline


def run_calibration_run(args: Namespace, *, root: Path) -> int:
    run_id = _run_id(args)
    mode = str(getattr(args, "mode", "bootstrap"))
    profile = str(getattr(args, "profile", "full"))
    run_dir = _report_dir(root, run_id, str(getattr(args, "report_root", "") or ""))
    run_dir.mkdir(parents=True, exist_ok=True)
    reuse_existing = not bool(getattr(args, "no_reuse_existing", False))
    resume_requested = bool(getattr(args, "resume", False))

    targets_path = run_dir / "targets.json"
    if not targets_path.exists():
        raise RuntimeError("targets.json missing. Run enumerate-targets first.")
    targets_payload = _read_json(targets_path)
    targets = targets_payload.get("targets", [])
    if not isinstance(targets, list):
        targets = []
    writer_contracts = _safe_yaml(root / "config" / "s0" / "writer_prompt_contracts.yaml")
    judge_contracts = _safe_yaml(root / "config" / "s0" / "judge_prompt_contracts.yaml")
    writer_model = str(os.environ.get("S0_WRITER_MODEL", "")).strip()
    judge_model = str(os.environ.get("S0_JUDGE_MODEL", "")).strip() or writer_model
    fingerprint_path = run_dir / "calibration_resume_fingerprint.json"
    resume_state_path = run_dir / "resume_state.json"
    fingerprint_record = _build_calibration_fingerprint(
        mode=mode,
        profile=profile,
        targets_payload=targets_payload,
        writer_contracts=writer_contracts,
        judge_contracts=judge_contracts,
        writer_model=writer_model,
        judge_model=judge_model,
    )
    existing_fingerprint = _read_json(fingerprint_path) if fingerprint_path.exists() else {}
    existing_digest = str(existing_fingerprint.get("fingerprint_digest", ""))
    fingerprint_digest = str(fingerprint_record.get("fingerprint_digest", ""))
    fingerprint_match = (not existing_digest) or (existing_digest == fingerprint_digest)

    selected = select_calibration_targets(targets)
    target_prompt_by_id = {
        str(t.get("target_id", "")): str(t.get("prompt_id", ""))
        for t in selected
        if isinstance(t, dict)
    }

    startup_summary = evaluate_startup_checklist(
        mode=mode,
        selected=selected,
        writer_contracts=writer_contracts,
        writer_model=writer_model,
        run_dir=run_dir,
        root=root,
        reuse_existing=reuse_existing,
        resume_requested=resume_requested,
        existing_digest=existing_digest,
        fingerprint_match=fingerprint_match,
        source_text=Path(__file__).read_text(encoding="utf-8"),
        is_allowed_resume_artifact=_is_allowed_resume_artifact,
    )
    startup_failures = [str(x) for x in startup_summary.get("failures", [])]
    resume_mode = bool(startup_summary.get("resume_mode", False))
    resume_candidate = bool(startup_summary.get("resume_candidate", False))
    unknown_existing = [str(x) for x in startup_summary.get("unknown_existing", [])]

    startup_report = {
        "run_id": run_id,
        "mode": mode,
        "resume_requested": resume_requested,
        "resume_mode": resume_mode,
        "resume_candidate": resume_candidate,
        "fingerprint_match": fingerprint_match,
        "resume_unknown_artifacts": unknown_existing,
        "status": "pass" if not startup_failures else "fail",
        "failures": startup_failures,
    }
    _write_json(run_dir / "startup_checklist_report.json", startup_report)
    if startup_failures:
        raise RuntimeError(f"Startup checklist failed: {startup_failures}")

    _write_json(fingerprint_path, fingerprint_record)

    prior_resume_state = _read_json(resume_state_path) if resume_state_path.exists() else {}
    attempt_index = int(prior_resume_state.get("attempt_index", 0)) + 1

    core_report_preexisting = (run_dir / "core_docs_eval_report.json").exists()
    rust_report_preexisting = (run_dir / "rust_reference_eval_report.json").exists()
    _write_json(
        resume_state_path,
        {
            "run_id": run_id,
            "attempt_index": attempt_index,
            "resume_requested": resume_requested,
            "resume_mode": resume_mode,
            "reuse_existing": reuse_existing,
            "fingerprint_digest": fingerprint_digest,
            "fingerprint_match": fingerprint_match,
            "reused_artifacts": [],
            "remaining_work_executed": [],
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )

    _write_json(
        run_dir / "calibration_target_rationale.json",
        {
            "run_id": run_id,
            "selection_policy": "deterministic_prompt_subset_v1",
            "selected_count": len(selected),
            "targets": selected,
        },
    )

    core_report_path, core = _run_eval_for_corpus(
        root, run_dir, "core_docs", reuse_existing=reuse_existing
    )
    rust_report_path, rust = _run_eval_for_corpus(
        root, run_dir, "rust_reference", reuse_existing=reuse_existing
    )

    reused_artifacts: list[str] = []
    remaining_work_executed: list[str] = []
    if reuse_existing and core_report_preexisting:
        reused_artifacts.append("core_docs_eval_report.json")
    else:
        remaining_work_executed.append("core_docs_eval_report.json")
    if reuse_existing and rust_report_preexisting:
        reused_artifacts.append("rust_reference_eval_report.json")
    else:
        remaining_work_executed.append("rust_reference_eval_report.json")
    _write_json(
        resume_state_path,
        {
            "run_id": run_id,
            "attempt_index": attempt_index,
            "resume_requested": resume_requested,
            "resume_mode": resume_mode,
            "reuse_existing": reuse_existing,
            "fingerprint_digest": fingerprint_digest,
            "fingerprint_match": fingerprint_match,
            "reused_artifacts": reused_artifacts,
            "remaining_work_executed": remaining_work_executed,
            "updated_at": datetime.now(UTC).isoformat(),
            "core_report_path": str(core_report_path),
            "rust_report_path": str(rust_report_path),
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

    context = prepare_exemplar_and_style_context(run_dir=run_dir, run_id=run_id, root=root)
    guidelines_repo_root = context["guidelines_repo_root"]
    row_map = context["row_map"]
    exemplar_lookup = context["exemplar_lookup"]
    writer_root = context["writer_root"]
    style_bundle = context["style_bundle"]
    std_lookup = context["std_lookup"]
    fls_stats = context["fls_stats"]
    convention_spec = context["convention_spec"]

    exemplar_selection_trace: list[dict[str, Any]] = []
    synthesis_input_trace: list[dict[str, Any]] = []
    draft_rows: list[dict[str, Any]] = []
    analysis_rows: list[dict[str, Any]] = []

    _write_json(
        run_dir / "lookup_status.json",
        {
            "run_id": run_id,
            "stdlib_entries": len(std_lookup),
            "stdlib_source": "core_docs_db" if CORE_DOCS_DB_PATH.exists() else "fallback",
            "fls_spec_db": fls_stats,
            "fls_id_validation": "spec.lock",
        },
    )
    fls_matching_report = validate_fls_matching()
    _write_json(run_dir / "fls_matching_validation.json", fls_matching_report)
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

    pipeline = execute_writer_validation_pipeline(
        root=root,
        run_dir=run_dir,
        run_id=run_id,
        selected_rows=selected_rows,
        row_map=row_map,
        exemplar_lookup=exemplar_lookup,
        target_prompt_by_id=target_prompt_by_id,
        writer_root=writer_root,
        writer_contracts=writer_contracts,
        judge_contracts=judge_contracts,
        guidelines_repo_root=guidelines_repo_root,
        std_lookup=std_lookup,
        fls_stats=fls_stats,
        convention_spec=convention_spec,
        mode=mode,
        writer_model=writer_model,
        judge_model=judge_model,
        core_gate=core_gate,
        rust_gate=rust_gate,
    )
    draft_rows = pipeline['draft_rows']
    synthesis_input_trace = pipeline['synthesis_input_trace']
    publishable_blocked = bool(pipeline['publishable_blocked'])
    evidence_gate_status = str(pipeline['evidence_gate_status'])
    citation_resolution_status = str(pipeline['citation_resolution_status'])
    conformance_report = pipeline['conformance_report']
    shape_all = bool(pipeline['shape_all'])
    non_abstain_drafts = pipeline['non_abstain_drafts']
    gate_policy_cfg = pipeline['gate_policy_cfg']
    convention_retry_budget = int(pipeline['convention_retry_budget'])
    total_retries = int(pipeline['total_retries'])
    retry_rate = float(pipeline['retry_rate'])
    retry_depth_variant = str(pipeline['retry_depth_variant'])

    stage_b_out = execute_stage_b_pipeline(
        run_dir=run_dir,
        root=root,
        run_id=run_id,
        draft_rows=draft_rows,
        judge_model=judge_model,
        publishable_blocked=publishable_blocked,
        evidence_gate_status=evidence_gate_status,
        citation_resolution_status=citation_resolution_status,
        conformance_report=conformance_report,
        shape_all=shape_all,
        core_gate=core_gate,
        rust_gate=rust_gate,
    )
    stage_b_judges = [str(name) for name in stage_b_out.get("stage_b_judges", [])]
    candidate_grade_count = int(stage_b_out.get("candidate_grade_count", 0) or 0)
    review_count = int(stage_b_out.get("review_count", 0) or 0)
    abstain_rate = float(stage_b_out.get("abstain_rate", 0.0) or 0.0)
    embarrassing_failure_count = int(stage_b_out.get("embarrassing_failure_count", 0) or 0)
    gate_passed = bool(stage_b_out.get("gate_passed", False))

    emit_calibration_reports(
        run_dir=run_dir,
        run_id=run_id,
        core_report_path=core_report_path,
        rust_report_path=rust_report_path,
        targets_path=targets_path,
        core_summary=core_summary,
        core_gate=core_gate,
        rust_summary=rust_summary,
        rust_gate=rust_gate,
        draft_rows=draft_rows,
        shape_all=shape_all,
        candidate_grade_count=candidate_grade_count,
        embarrassing_failure_count=embarrassing_failure_count,
        gate_passed=gate_passed,
        convention_retry_budget=convention_retry_budget,
        gate_policy_cfg=gate_policy_cfg,
        total_retries=total_retries,
        retry_rate=retry_rate,
        retry_depth_variant=retry_depth_variant,
        stage_b_judges=stage_b_judges,
        non_abstain_drafts=non_abstain_drafts,
        mode=mode,
        root=root,
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
