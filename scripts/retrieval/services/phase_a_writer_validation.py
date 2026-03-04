from __future__ import annotations

from typing import Any

from retrieval.services import s0_phase_a_impl as _impl
from retrieval.services.phase_a_writer_reports import emit_tail_reports

globals().update({name: getattr(_impl, name) for name in dir(_impl)})


def execute_validation_and_rendering_pipeline(
    *,
    root: Path,
    run_dir: Path,
    run_id: str,
    selected_rows: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    mode: str,
    role_contracts: dict[str, Any],
    target_prompt_by_id: dict[str, str],
    writer_evidence_rows: list[dict[str, Any]],
    writer_example_rows: list[dict[str, Any]],
    writer_rationale_rows: list[dict[str, Any]],
    writer_metadata_rows: list[dict[str, Any]],
    draft_rows: list[dict[str, Any]],
    analysis_rows: list[dict[str, Any]],
    exemplar_selection_trace: list[dict[str, Any]],
    synthesis_input_trace: list[dict[str, Any]],
    guidelines_repo_root: Path,
    gate_policy_cfg: dict[str, Any],
    convention_retry_budget: int,
    total_retries: int,
    retry_rate: float,
    retry_depth_variant: str,
) -> dict[str, Any]:
    evidence_contract = (
        role_contracts.get("evidence_synthesizer", {}) if isinstance(role_contracts, dict) else {}
    )
    evidence_required = _required_fields(
        evidence_contract.get("required_output_schema", {})
        if isinstance(evidence_contract, dict)
        else {}
    )
    evidence_forbidden = (
        evidence_contract.get("forbidden_patterns", [])
        if isinstance(evidence_contract, dict)
        else []
    )
    if not isinstance(evidence_forbidden, list):
        evidence_forbidden = []
    gate_policy_cfg = _safe_yaml(root / "config" / "s0" / "s0_gate_policy.yaml")
    must_pass_prompt_ids = (
        gate_policy_cfg.get("must_pass_prompt_ids", []) if isinstance(gate_policy_cfg, dict) else []
    )
    if not isinstance(must_pass_prompt_ids, list):
        must_pass_prompt_ids = []
    must_pass_prompt_ids = [str(x) for x in must_pass_prompt_ids if str(x).strip()]
    synonyms_cfg = _safe_yaml(root / "config" / "s0" / "construct_synonyms.yaml")
    alias_map = _synonym_alias_map(synonyms_cfg)
    evidence_ids_by_target: dict[str, list[str]] = {}
    for selected_row in selected_rows:
        if not isinstance(selected_row, dict):
            continue
        target_raw = selected_row.get("target")
        target_obj: dict[str, Any] = target_raw if isinstance(target_raw, dict) else {}
        target_id = str(target_obj.get("target_id", "")).strip()
        chunk_ids_raw = selected_row.get("top_chunk_ids")
        chunk_ids: list[str] = []
        if isinstance(chunk_ids_raw, list):
            chunk_ids = [str(x) for x in chunk_ids_raw if str(x).strip()]
        evidence_ids_by_target[target_id] = chunk_ids
    evidence_lookup_by_target: dict[str, dict[str, dict[str, Any]]] = {}
    for selected_row in selected_rows:
        if not isinstance(selected_row, dict):
            continue
        target_obj: dict[str, Any] = {}
        target_raw = selected_row.get("target")
        if isinstance(target_raw, dict):
            target_obj = target_raw
        target_id = str(target_obj.get("target_id", ""))
        snippets: list[dict[str, Any]] = []
        snippets_raw = selected_row.get("snippets")
        if isinstance(snippets_raw, list):
            snippets = [item for item in snippets_raw if isinstance(item, dict)]
        lookup: dict[str, dict[str, Any]] = {}
        for snippet in snippets:
            evidence_id = str(snippet.get("chunk_uid", "")).strip()
            if not evidence_id:
                continue
            lookup[evidence_id] = {
                "source": str(snippet.get("document_id", "")).strip()
                or "calibration_evidence_bundle",
                "anchor": str(snippet.get("anchor", "")).strip() or evidence_id,
                "text": str(snippet.get("text", "")),
            }
        evidence_lookup_by_target[target_id] = lookup

    evidence_gate_rows: list[dict[str, Any]] = []
    normalization_rows: list[dict[str, Any]] = []
    normalized_writer_evidence_rows: list[dict[str, Any]] = []
    evidence_schema_pass = 0
    evidence_normative_pass = 0
    evidence_banned_pass = 0
    for idx, row in enumerate(writer_evidence_rows, start=1):
        if not isinstance(row, dict):
            continue
        target_id = str(row.get("target_id", "")).strip()
        prompt_id = str(row.get("prompt_id", target_prompt_by_id.get(target_id, ""))).strip()
        evidence_ids = evidence_ids_by_target.get(target_id, [])
        supplemental_text = [
            str(row.get("hazard", "")),
            str(row.get("mechanism", "")),
            str(row.get("mitigation", "")),
        ]
        claim_rows_raw = row.get("claim_to_evidence_map")
        normalized_claims, claim_patterns = _normalize_claim_map(
            claim_rows_raw, target_id=target_id
        )
        for claim in normalized_claims:
            supplemental_text.append(str(claim.get("claim_text", "")))
        normalized_scope, scope_patterns = _normalize_construct_scope(
            row.get("construct_scope"),
            supplemental_text=supplemental_text,
            alias_map=alias_map,
        )
        canonical_pre = isinstance(row.get("construct_scope"), list) and isinstance(
            row.get("claim_to_evidence_map"), list
        )
        normalized_row = dict(row)
        normalized_row["target_id"] = target_id
        normalized_row["prompt_id"] = prompt_id
        normalized_row["construct_scope"] = normalized_scope
        normalized_row["claim_to_evidence_map"] = normalized_claims
        normalized_writer_evidence_rows.append(normalized_row)

        row_missing = _ensure_required_fields(
            "evidence_synthesizer", normalized_row, evidence_required
        )
        if not target_id:
            row_missing.append("evidence_synthesizer:missing_required:target_id")
        if not prompt_id:
            row_missing.append("evidence_synthesizer:missing_required:prompt_id")
        if not isinstance(normalized_scope, list) or not normalized_scope:
            row_missing.append("evidence_synthesizer:missing_required:construct_scope")
        if not normalized_claims:
            row_missing.append("evidence_synthesizer:missing_required:claim_to_evidence_map")
        schema_ok = not row_missing

        construct_scope = [str(x) for x in normalized_scope]
        construct_scope_normalized = [_normalize_text(x) for x in construct_scope if str(x).strip()]
        evidence_id_set = {str(x) for x in evidence_ids}
        reason_codes: list[str] = []
        normative_ok = False
        for claim in normalized_claims:
            claim_text = _normalize_text(str(claim.get("claim_text", "")))
            refs_raw = claim.get("evidence_refs")
            refs: list[dict[str, Any]] = []
            if isinstance(refs_raw, list):
                refs = [item for item in refs_raw if isinstance(item, dict)]
            if not refs:
                reason_codes.append("missing_evidence_refs")
                continue
            ref_ids = {
                str(item.get("evidence_id", item.get("chunk_id", ""))).strip()
                for item in refs
                if isinstance(item, dict)
            }
            if evidence_id_set and not (ref_ids & evidence_id_set):
                reason_codes.append("evidence_id_not_in_bundle")
                continue
            token_hit = any(term in claim_text for term in construct_scope_normalized if term)
            if not token_hit:
                reason_codes.append("claim_not_construct_specific")
                continue
            if not claim_text:
                reason_codes.append("missing_claim_rows")
                continue
            if not construct_scope_normalized:
                reason_codes.append("missing_construct_scope_terms")
                continue
            if token_hit and ref_ids:
                normative_ok = True
                break
        if not normalized_claims:
            reason_codes.append("missing_claim_rows")
        if not construct_scope_normalized:
            reason_codes.append("missing_construct_scope_terms")

        row_text = _normalize_text(json.dumps(normalized_row, sort_keys=True))
        banned_ok = not any(_normalize_text(str(pat)) in row_text for pat in evidence_forbidden)
        evidence_schema_pass += int(schema_ok)
        evidence_normative_pass += int(normative_ok)
        evidence_banned_pass += int(banned_ok)
        dedup_reasons = sorted(set(reason_codes))
        evidence_gate_rows.append(
            {
                "target_id": target_id,
                "prompt_id": prompt_id,
                "schema_ok": schema_ok,
                "normative_claim_ok": normative_ok,
                "banned_pattern_ok": banned_ok,
                "missing_required": row_missing,
                "reason_codes": dedup_reasons,
            }
        )
        normalization_rows.append(
            {
                "target_id": target_id,
                "prompt_id": prompt_id,
                "canonical_pre_normalization": canonical_pre,
                "patterns_detected": sorted(set(claim_patterns + scope_patterns)),
                "transforms_applied": [
                    "normalize_claim_to_evidence_map",
                    "normalize_construct_scope",
                    "synthesize_claim_id",
                ],
                "claims_out": len(normalized_claims),
            }
        )

    writer_evidence_rows = normalized_writer_evidence_rows
    evidence_by_draft = {str(row.get("draft_id", "")): row for row in writer_evidence_rows}
    for draft in draft_rows:
        draft_id = str(draft.get("draft_id", ""))
        normalized_evidence = evidence_by_draft.get(draft_id, {})
        scope = (
            normalized_evidence.get("construct_scope")
            if isinstance(normalized_evidence, dict)
            else []
        )
        draft["construct_terms"] = [str(x) for x in scope] if isinstance(scope, list) else []

    abstain_expected_count = len(
        [
            t
            for t in selected
            if bool(t.get("abstain_expected", False)) or bool(t.get("expect_abstain", False))
        ]
    )
    viable_targets = max(1, len(selected) - abstain_expected_count)
    required_normative = max(1, int((0.60 * viable_targets) + 0.9999))
    must_pass_failures = [
        row
        for row in evidence_gate_rows
        if str(row.get("prompt_id", "")) in must_pass_prompt_ids
        and not bool(row.get("normative_claim_ok", False))
    ]
    evidence_gate_status = "pass"
    if (
        evidence_schema_pass < 3
        or evidence_normative_pass < required_normative
        or evidence_banned_pass < 3
    ):
        evidence_gate_status = "fail"
    if must_pass_failures:
        evidence_gate_status = "fail"

    canonical_rate = 0.0
    if normalization_rows:
        canonical_rate = len(
            [
                row
                for row in normalization_rows
                if bool(row.get("canonical_pre_normalization", False))
            ]
        ) / float(len(normalization_rows))
    _write_json(
        run_dir / "normalization_report.json",
        {
            "run_id": run_id,
            "status": "pass" if normalization_rows else "fail",
            "canonical_rate": canonical_rate,
            "required_normative": required_normative,
            "viable_targets": viable_targets,
            "results": normalization_rows,
        },
    )
    _write_json(
        run_dir / "evidence_synthesizer_gate_report.json",
        {
            "run_id": run_id,
            "status": evidence_gate_status,
            "schema_valid_count": evidence_schema_pass,
            "normative_claim_count": evidence_normative_pass,
            "required_normative": required_normative,
            "viable_targets": viable_targets,
            "banned_pattern_count": evidence_banned_pass,
            "must_pass_prompt_ids": must_pass_prompt_ids,
            "must_pass_failures": must_pass_failures,
            "results": evidence_gate_rows,
        },
    )
    diagnostic_lane_enabled = False
    if evidence_gate_status != "pass":
        diagnostic_lane_enabled = True
        reports_root = root / ".cache" / "sqlite_kb" / "reports"
        prior_fail = False
        if reports_root.exists():
            prior_runs = sorted(
                [p for p in reports_root.iterdir() if p.is_dir() and p.name != run_id],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for prior in prior_runs:
                prior_report = prior / "evidence_synthesizer_gate_report.json"
                if not prior_report.exists():
                    continue
                prior_payload = _read_json(prior_report)
                if str(prior_payload.get("status", "")) == "fail":
                    prior_fail = True
                break
        top_failure_patterns: list[str] = []
        if evidence_schema_pass < 3:
            top_failure_patterns.append("schema_noncompliance")
        if evidence_normative_pass < 3:
            top_failure_patterns.append("missing_construct_specific_normative_claim")
        if evidence_banned_pass < 3:
            top_failure_patterns.append("forbidden_pattern_regression")
        escalation = {
            "run_id": run_id,
            "status": "escalated",
            "trigger": "evidence_synthesizer_exit_gate_failed",
            "repeated_gate_miss": prior_fail,
            "diagnostic_lane_enabled": True,
            "top_failure_patterns": top_failure_patterns[:3],
            "options": [
                "Prompt redesign using stronger worked examples and tighter forbidden patterns.",
                "Model/decode adjustment for writer roles.",
                "Temporary scope reduction of targets for prompt hardening validation.",
            ],
        }
        _write_json(run_dir / "evidence_synthesizer_escalation_report.json", escalation)

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

    resolved_metadata_by_draft: dict[str, dict[str, Any]] = {}
    citation_resolution_rows: list[dict[str, Any]] = []
    for draft in draft_rows:
        draft_id = str(draft.get("draft_id", ""))
        target_id = str(draft.get("target_id", ""))
        prompt_id = str(draft.get("target_prompt_id", ""))
        metadata_payload = metadata_by_draft.get(draft_id, {})
        evidence_payload = evidence_by_draft.get(draft_id, {})
        evidence_ids = [str(x) for x in (draft.get("evidence_chunk_ids") or []) if str(x).strip()]
        evidence_lookup = evidence_lookup_by_target.get(target_id, {})

        resolved_rows, resolve_patterns = _resolve_bibliography_rows(
            metadata_payload if isinstance(metadata_payload, dict) else {},
            prompt_id=prompt_id,
            run_id=run_id,
            evidence_lookup=evidence_lookup,
            evidence_ids=evidence_ids,
            construct_terms=[str(x) for x in (draft.get("construct_terms") or [])],
        )
        cited_keys = [
            str(row.get("citation_key", "")).strip()
            for row in resolved_rows
            if str(row.get("citation_key", "")).strip()
        ]
        unresolved = [
            row
            for row in resolved_rows
            if not (
                str((row.get("locator") or {}).get("url", "")).strip()
                or str((row.get("locator") or {}).get("path", "")).strip()
                or str((row.get("locator") or {}).get("paragraph_id", "")).strip()
            )
        ]
        resolution_ok = bool(resolved_rows) and not unresolved
        resolved_metadata_by_draft[draft_id] = {
            "target_id": target_id,
            "prompt_id": prompt_id,
            "citation_key_prefix": prompt_id,
            "bibliography_rows": resolved_rows,
            "citation_keys": cited_keys,
            "resolution_ok": resolution_ok,
        }
        citation_resolution_rows.append(
            {
                "draft_id": draft_id,
                "target_id": target_id,
                "prompt_id": prompt_id,
                "resolution_ok": resolution_ok,
                "row_count": len(resolved_rows),
                "unresolved_count": len(unresolved),
                "patterns": resolve_patterns,
            }
        )

    draft_status_by_id = {str(d.get("draft_id", "")): str(d.get("status", "")) for d in draft_rows}
    citation_resolution_status = "pass"
    for row in citation_resolution_rows:
        draft_id = str(row.get("draft_id", ""))
        if draft_status_by_id.get(draft_id) in {"abstain", "diagnostic"}:
            continue
        if not bool(row.get("resolution_ok", False)):
            citation_resolution_status = "fail"
            break
    _write_json(
        run_dir / "citation_resolution_report.json",
        {
            "run_id": run_id,
            "status": citation_resolution_status,
            "results": citation_resolution_rows,
        },
    )

    rst_dir = run_dir / "generated_guidelines_rst"
    rst_dir.mkdir(parents=True, exist_ok=True)
    for stale in rst_dir.glob("*.rst"):
        stale.unlink()
    rerendered_dir = run_dir / "rerendered_rst"
    rerendered_dir.mkdir(parents=True, exist_ok=True)
    for stale in rerendered_dir.glob("*.rst"):
        stale.unlink()
    (rst_dir / "README.md").write_text(
        "Generated calibration guideline files for Phase A.\n", encoding="utf-8"
    )

    export_files: list[dict[str, Any]] = []
    shape_results: list[dict[str, Any]] = []
    diff_results: list[dict[str, Any]] = []
    publishable_blocked = evidence_gate_status != "pass"
    if publishable_blocked:
        _write_json(
            run_dir / "diagnostic_lane_report.json",
            {
                "run_id": run_id,
                "non_publishable": True,
                "reason": "evidence_synthesizer_gate_failed",
                "advisory_only_stage_b": True,
            },
        )
    for draft in draft_rows:
        if str(draft.get("status", "")) in {"abstain", "diagnostic"}:
            continue
        if publishable_blocked:
            file_name = f"{str(draft.get('target_prompt_id', '')).lower().replace('_', '-')}.rst"
            shape_results.append(
                {
                    "file": file_name,
                    "shape_match": False,
                    "missing_required_blocks": ["publishable_blocked_by_evidence_gate"],
                    "metadata_key_violations": ["publishable_blocked_by_evidence_gate"],
                    "candidate_shape_ok": False,
                }
            )
            continue
        prompt_id = str(draft["target_prompt_id"])
        construct_terms = [str(x) for x in (draft.get("construct_terms") or [])]
        fls_info = _resolve_fls_for_construct_safe(construct_terms)
        fls_id = str(fls_info.get("paragraph_id", "fls_UNRESOLVED"))
        metadata_payload = resolved_metadata_by_draft.get(str(draft.get("draft_id", "")), {})
        fls_candidate = str(metadata_payload.get("fls_candidate", "")).strip()
        if fls_candidate.startswith("fls_") and validate_fls_id(fls_candidate):
            fls_id = fls_candidate
        title = str(draft["title"]).strip()
        row_id = (draft.get("table1_rows") or [""])[0]
        tag_row = f"table1-{row_id}" if row_id else "table1-unknown"
        tag_category = str(draft.get("category", "safety-control")).replace(" ", "-")
        tag_corpus = str(draft.get("corpus", "s0"))
        evidence_payload = evidence_by_draft.get(str(draft.get("draft_id", "")), {})
        example_payload = example_by_draft.get(str(draft.get("draft_id", "")), {})
        rationale_payload = rationale_by_draft.get(str(draft.get("draft_id", "")), {})
        bibliography_rows = (
            metadata_payload.get("bibliography_rows") if isinstance(metadata_payload, dict) else []
        )
        if not isinstance(bibliography_rows, list):
            bibliography_rows = []
        citation_keys = [
            str(row.get("citation_key", "")).strip()
            for row in bibliography_rows
            if isinstance(row, dict) and str(row.get("citation_key", "")).strip()
        ]
        renderer_input = RendererInput(
            title=title,
            guideline_text=str(draft.get("guideline", "")),
            rationale_text=str(rationale_payload.get("rationale_text", draft.get("rationale", ""))),
            non_compliant_narrative=str(
                example_payload.get(
                    "non_compliant_narrative",
                    "Non-compliant example demonstrates hazard trigger.",
                )
            ),
            non_compliant_code=str(example_payload.get("non_compliant_code", "fn main() {}")),
            compliant_narrative=str(
                example_payload.get(
                    "compliant_narrative", "Compliant example demonstrates mitigation."
                )
            ),
            compliant_code=str(example_payload.get("compliant_code", "fn main() {}")),
            bibliography_rows=[row for row in bibliography_rows if isinstance(row, dict)],
            non_compliant_mode=str(draft.get("example_execution_mode", "runnable")),
            compliant_mode=str(example_payload.get("compliant_mode", "runnable")),
            non_compliant_miri_intent=str(example_payload.get("non_compliant_miri_intent", "none")),
            compliant_miri_intent=str(example_payload.get("compliant_miri_intent", "none")),
            category=str(draft.get("category", "advisory")),
            normative_strength=str(draft.get("strength", "should")),
            decidability=str(metadata_payload.get("decidability", "decidable")),
            scope=str(metadata_payload.get("scope", "module")),
            tags=[tag_category, tag_row, tag_corpus],
            citation_keys_used=citation_keys,
            prompt_id=prompt_id,
            exemplar_ids_used=[str(x) for x in (draft.get("exemplar_ids_used") or []) if str(x)],
        )
        section_text = render_guideline_rst(renderer_input, guidelines_repo_root).rst
        file_name = f"{prompt_id.lower().replace('_', '-')}.rst"
        output_path = rst_dir / file_name
        output_path.write_text(section_text, encoding="utf-8")
        (rerendered_dir / file_name).write_text(section_text, encoding="utf-8")
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
    conformance_report = validate_generated_rst_conformance(
        run_dir,
        source_dir_name="generated_guidelines_rst",
    )
    _write_json(run_dir / "output_conformance_report.json", conformance_report)
    _write_json(run_dir / "format_diff_report.json", {"run_id": run_id, "results": diff_results})
    non_abstain_drafts = [
        row for row in draft_rows if str(row.get("status", "")) not in {"abstain", "diagnostic"}
    ]
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
    synonyms_cfg = _safe_yaml(root / "config" / "s0" / "construct_synonyms.yaml")
    synonyms_map = synonyms_cfg.get("synonyms", {}) if isinstance(synonyms_cfg, dict) else {}
    if not isinstance(synonyms_map, dict):
        synonyms_map = {}

    duplicate_findings: list[dict[str, Any]] = []
    for row in sim_matrix:
        left_id = str(row.get("left_draft_id", ""))
        right_id = str(row.get("right_draft_id", ""))
        if not left_id or not right_id or left_id >= right_id:
            continue
        score = float(row.get("jaccard_4gram", 0.0) or 0.0)
        if score <= 0.60:
            continue
        left = next((d for d in non_abstain_drafts if str(d.get("draft_id", "")) == left_id), {})
        right = next((d for d in non_abstain_drafts if str(d.get("draft_id", "")) == right_id), {})
        left_terms = {str(x).lower() for x in (left.get("construct_terms") or [])}
        right_terms = {str(x).lower() for x in (right.get("construct_terms") or [])}
        same_family = bool(left_terms & right_terms)
        status = "review" if mode == "bootstrap" and same_family else "block"
        duplicate_findings.append(
            {
                "left_draft_id": left_id,
                "right_draft_id": right_id,
                "jaccard_4gram": score,
                "same_construct_family": same_family,
                "status": status,
            }
        )
    duplicate_gate_status = (
        "pass" if not any(x["status"] == "block" for x in duplicate_findings) else "fail"
    )
    _write_json(
        run_dir / "duplicate_similarity_gate_report.json",
        {
            "run_id": run_id,
            "mode": mode,
            "status": duplicate_gate_status,
            "findings": duplicate_findings,
        },
    )

    alignment_findings: list[dict[str, Any]] = []
    for draft in non_abstain_drafts:
        draft_id = str(draft.get("draft_id", ""))
        target_id = str(draft.get("target_id", ""))
        evidence = evidence_by_draft.get(draft_id, {})
        claim_rows: list[dict[str, Any]] = []
        if isinstance(evidence, dict):
            claim_map = evidence.get("claim_to_evidence_map")
            if isinstance(claim_map, list):
                claim_rows = [item for item in claim_map if isinstance(item, dict)]
        target_lookup = evidence_lookup_by_target.get(target_id, {})
        construct_terms = [str(x) for x in (draft.get("construct_terms") or [])]
        term_set = {t.lower() for t in construct_terms}
        for term in list(term_set):
            synonyms = synonyms_map.get(term)
            if not isinstance(synonyms, list):
                synonyms = []
            for syn in synonyms:
                term_set.add(str(syn).lower())
        claim_status = True
        for claim in claim_rows:
            if not isinstance(claim, dict):
                continue
            refs_raw = claim.get("evidence_refs")
            refs: list[dict[str, Any]] = []
            if isinstance(refs_raw, list):
                refs = [item for item in refs_raw if isinstance(item, dict)]
            aligned = False
            for ref in refs:
                evidence_id = str(ref.get("evidence_id", ref.get("chunk_id", ""))).strip()
                excerpt = _normalize_text(str(ref.get("excerpt_text", "")))
                if not excerpt and evidence_id:
                    excerpt = _normalize_text(
                        str(target_lookup.get(evidence_id, {}).get("text", ""))
                    )
                if any(term and term in excerpt for term in term_set):
                    aligned = True
                    break
            if not aligned:
                claim_status = False
        alignment_findings.append({"draft_id": draft_id, "aligned": claim_status})
    alignment_status = (
        "pass" if all(x.get("aligned", False) for x in alignment_findings) else "fail"
    )
    _write_json(
        run_dir / "construct_evidence_alignment_report.json",
        {
            "run_id": run_id,
            "status": alignment_status,
            "results": alignment_findings,
        },
    )

    example_semantics_results: list[dict[str, Any]] = []
    for draft in non_abstain_drafts:
        mode_value = str(draft.get("example_execution_mode", "runnable"))
        runtime_hazard = bool(draft.get("runtime_hazard_target", False))
        valid = True
        if runtime_hazard and mode_value == "compile_fail":
            valid = False
        example_semantics_results.append(
            {
                "draft_id": str(draft.get("draft_id", "")),
                "mode": mode_value,
                "runtime_hazard_target": runtime_hazard,
                "valid": valid,
            }
        )
    example_semantics_status = (
        "pass" if all(x.get("valid", False) for x in example_semantics_results) else "fail"
    )
    _write_json(
        run_dir / "example_execution_semantics_report.json",
        {
            "run_id": run_id,
            "status": example_semantics_status,
            "results": example_semantics_results,
        },
    )

    emit_tail_reports(
        run_dir=run_dir,
        run_id=run_id,
        non_abstain_drafts=non_abstain_drafts,
        shape_all=shape_all,
        shape_results=shape_results,
        synthesis_input_trace=synthesis_input_trace,
    )

    return {
        "draft_rows": draft_rows,
        "synthesis_input_trace": synthesis_input_trace,
        "publishable_blocked": publishable_blocked,
        "evidence_gate_status": evidence_gate_status,
        "citation_resolution_status": citation_resolution_status,
        "conformance_report": conformance_report,
        "shape_all": shape_all,
        "non_abstain_drafts": non_abstain_drafts,
        "gate_policy_cfg": gate_policy_cfg,
        "convention_retry_budget": convention_retry_budget,
        "total_retries": total_retries,
        "retry_rate": retry_rate,
        "retry_depth_variant": retry_depth_variant,
    }
