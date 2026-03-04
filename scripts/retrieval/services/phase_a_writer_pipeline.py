from __future__ import annotations

from typing import Any

from retrieval.services import s0_phase_a_impl as _impl

globals().update({name: getattr(_impl, name) for name in dir(_impl)})
from retrieval.services.phase_a_writer_validation import execute_validation_and_rendering_pipeline

def execute_writer_validation_pipeline(
    *,
    root: Path,
    run_dir: Path,
    run_id: str,
    selected_rows: list[dict[str, Any]],
    row_map: dict[str, list[str]],
    exemplar_lookup: dict[str, dict[str, Any]],
    target_prompt_by_id: dict[str, str],
    writer_root: Path,
    writer_contracts: dict[str, Any],
    judge_contracts: dict[str, Any],
    guidelines_repo_root: Path,
    std_lookup: dict[str, Any],
    fls_stats: dict[str, Any],
    convention_spec: dict[str, Any],
    mode: str,
    writer_model: str,
    judge_model: str,
    core_gate: list[str],
    rust_gate: list[str],
) -> dict[str, Any]:
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

        synthesis_input_trace.append(
            {
                "target_id": target_id,
                "target_prompt_id": prompt_id,
                "exemplar_ids_used": selected_exemplars,
                "evidence_ids_used": row["top_chunk_ids"],
                "input_digest": hashlib.sha256(
                    json.dumps(
                        {
                            "target": target,
                            "selected_exemplars": selected_exemplars,
                            "evidence_ids": row["top_chunk_ids"],
                            "snippets": snippets,
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

    drafting_contract = _safe_yaml(root / "config" / "s0" / "drafting_prompt_contract.yaml")
    worked_pos = drafting_contract.get("worked_positive_examples", [])
    worked_neg = drafting_contract.get("worked_negative_examples", [])
    runtime_cfg = _safe_yaml(root / "config" / "s0" / "target_execution_modes.yaml")
    runtime_hazard_rows = {
        str(x)
        for x in (
            runtime_cfg.get("runtime_hazard_rows", []) if isinstance(runtime_cfg, dict) else []
        )
    }
    row_defaults = runtime_cfg.get("row_defaults", {}) if isinstance(runtime_cfg, dict) else {}
    prompt_overrides = (
        runtime_cfg.get("prompt_overrides", {}) if isinstance(runtime_cfg, dict) else {}
    )
    default_mode = (
        str(runtime_cfg.get("default_mode", "runnable"))
        if isinstance(runtime_cfg, dict)
        else "runnable"
    )
    writer_timeout = int(str(os.environ.get("S0_WRITER_TIMEOUT_SECONDS", "90")))

    style_excerpt = "\n".join(style_text.splitlines()[:80])
    budget_cfg = writer_contracts.get("injected_context_budgets", {})
    if not isinstance(budget_cfg, dict):
        budget_cfg = {}
    convention_budget = int(budget_cfg.get("convention_spec_tokens", 2000))
    std_budget = int(budget_cfg.get("std_lookup_tokens", 1000))
    exemplar_budget = int(budget_cfg.get("exemplar_tokens", 500))
    total_budget = int(budget_cfg.get("total_injected_tokens", 3500))

    exemplar_snippets_by_id: dict[str, str] = {}
    for entry in exemplar_entries:
        if not isinstance(entry, dict) or entry.get("status") != "ok":
            continue
        guideline_id = str(entry.get("guideline_id", ""))
        path_raw = str(entry.get("path", ""))
        if not guideline_id or not path_raw:
            continue
        path = Path(path_raw)
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        exemplar_snippets_by_id[guideline_id] = "\n".join(lines[:24])

    retry_depth_variant, max_role_retries, retry_resolution_rate = _load_retry_depth_policy(
        root, run_dir
    )
    gate_policy_cfg = _safe_yaml(root / "config" / "s0" / "s0_gate_policy.yaml")
    convention_retry_budget = int(
        gate_policy_cfg.get(
            "convention_retry_budget",
            gate_policy_cfg.get("max_convention_retries", CONVENTION_RETRY_BUDGET),
        )
    )
    non_abstain_targets = [
        row
        for row in selected_rows
        if not bool((row.get("target") or {}).get("expect_abstain", False))
        and not bool((row.get("target") or {}).get("abstain_expected", False))
    ]
    per_target_retry_budget = convention_retry_budget // max(len(non_abstain_targets), 1)
    target_retry_usage: dict[str, int] = {}
    target_lanes: dict[str, dict[str, str]] = {}
    role_validation_log: list[dict[str, Any]] = []
    retry_log: list[dict[str, Any]] = []
    writer_role_order = [
        "evidence_synthesizer",
        "amplification_author",
        "example_author",
        "rationale_author",
        "metadata_citation_curator",
    ]

    for row in selected_rows:
        target = row["target"]
        prompt_id = str(target.get("prompt_id", ""))
        corpus_name = str(target.get("corpus", ""))
        target_id = str(target.get("target_id", ""))
        row_id = _target_row(target)
        selected_exemplars = []
        for trace in exemplar_selection_trace:
            if str(trace.get("target_id", "")) == target_id:
                selected_exemplars = [str(x) for x in (trace.get("selected_exemplar_ids") or [])]
                break
        snippets = row.get("snippets") or []
        snippet_rows = [s for s in snippets if isinstance(s, dict)]
        evidence_ids = [str(x) for x in (row.get("top_chunk_ids") or [])]
        evidence_text = "\n\n".join(str(s.get("text", ""))[:700] for s in snippet_rows[:3])
        example_mode = str(prompt_overrides.get(prompt_id, row_defaults.get(row_id, default_mode)))

        role_order = writer_role_order
        role_outputs: dict[str, dict[str, Any]] = {}
        role_failures: list[str] = []
        draft_id = f"draft::{prompt_id.lower()}"

        for role_name in role_order:
            role_contract = (
                role_contracts.get(role_name) if isinstance(role_contracts, dict) else {}
            )
            if not isinstance(role_contract, dict):
                role_contract = {}
            required = _required_fields(role_contract.get("required_output_schema", {}))
            forbidden = role_contract.get("forbidden_patterns", [])
            forbidden = forbidden if isinstance(forbidden, list) else []
            prompt_template = str(role_contract.get("prompt_template_text", ""))
            role_input = {
                "target_id": target_id,
                "target_prompt_id": prompt_id,
                "table1_row": row_id,
                "corpus": corpus_name,
                "evidence_ids": evidence_ids,
                "evidence_snippets": snippet_rows,
                "evidence_text": evidence_text,
                "exemplar_ids": selected_exemplars,
                "worked_positive_examples": worked_pos,
                "worked_negative_examples": worked_neg,
                "style_excerpt": style_excerpt,
                "example_execution_mode": example_mode,
                "runtime_hazard_target": row_id in runtime_hazard_rows,
                "upstream_outputs": role_outputs,
            }

            if role_name == "evidence_synthesizer":
                construct_terms = _rust_like_tokens(evidence_text)[:12]
            else:
                scope_terms = role_outputs.get("evidence_synthesizer", {}).get(
                    "construct_scope", []
                )
                if isinstance(scope_terms, list):
                    construct_terms = [str(value) for value in scope_terms if str(value).strip()][
                        :12
                    ]
                else:
                    construct_terms = _rust_like_tokens(str(scope_terms))[:12]

            std_lookup_scoped = {
                key: value
                for key, value in std_lookup.items()
                if _is_relevant_to_construct(key, construct_terms)
            }
            std_lookup_payload, std_omitted = _truncate_mapping_for_budget(
                std_lookup_scoped,
                token_budget=std_budget,
                sort_terms=construct_terms,
            )

            exemplar_extracts = [
                {
                    "guideline_id": gid,
                    "snippet": exemplar_snippets_by_id.get(gid, ""),
                }
                for gid in selected_exemplars[:2]
            ]
            exemplar_extracts = [item for item in exemplar_extracts if item.get("snippet")]
            exemplar_payload, exemplar_omitted = _truncate_exemplar_extracts(
                exemplar_extracts,
                token_budget=exemplar_budget,
            )

            role_input["convention_spec"] = convention_spec
            role_input["std_lookup"] = std_lookup_payload
            role_input["exemplar_extracts"] = exemplar_payload
            rendered_prompt = (
                f"{prompt_template}\n\n"
                f"Output schema required fields: {required}\n"
                f"Forbidden patterns: {forbidden}\n"
                "Length and structure bounds: keep each narrative field between 40 and 220 words; "
                "code examples between 4 and 40 lines; no placeholder text.\n"
                f"Input context JSON:\n{json.dumps(role_input, indent=2, sort_keys=True)}"
            )
            prompt_template_id, _prompt_template_digest = _role_prompt(role_name)
            target_retry_remaining = max(
                per_target_retry_budget - target_retry_usage.get(target_id, 0),
                0,
            )
            allowed_retries = min(max_role_retries, target_retry_remaining)
            role_budget = 1 + allowed_retries
            role_latest_violations: list[RoleViolation] = []
            role_attempt_entries: list[dict[str, Any]] = []
            role_attempt_counter = 0

            def _parse_role_violations(output_payload: dict[str, Any]) -> list[str] | None:
                nonlocal role_latest_violations, role_attempt_counter
                role_attempt_counter += 1
                role_latest_violations = validate_role_output(
                    role_name,
                    output_payload,
                    convention_spec,
                    std_lookup,
                    construct_terms,
                    prompt_id,
                )
                role_attempt_entries.append(
                    {
                        "attempt": role_attempt_counter,
                        "violations": [
                            {
                                "check": violation.check,
                                "message": violation.message,
                                "severity": violation.severity,
                            }
                            for violation in role_latest_violations
                        ],
                    }
                )
                return [
                    violation.check
                    for violation in role_latest_violations
                    if violation.severity == "error"
                ]

            def _build_retry_prompt(initial: str, active_checks: list[str]) -> str:
                active_set = set(active_checks)
                active_violations = [
                    violation
                    for violation in role_latest_violations
                    if violation.severity == "error" and violation.check in active_set
                ]
                violations_text = "\n".join(
                    f"- [{violation.check}] {violation.message}" for violation in active_violations
                )
                if len(violations_text) > 8000:
                    violations_text = violations_text[:8000] + "\n[...truncated...]"
                next_attempt = min(role_attempt_counter + 1, role_budget)
                return (
                    f"{initial}\n\n"
                    f"=== RETRY (attempt {next_attempt}/{role_budget}) ===\n"
                    "Your previous output had these violations:\n"
                    f"{violations_text}\n"
                    "Please fix these issues in your output."
                )

            request_started_at = datetime.now(UTC).isoformat()
            retry_result = retry_with_violations(
                session_id=f"{run_id}:{prompt_id}:{role_name}",
                initial_prompt=rendered_prompt,
                parse_violations_fn=_parse_role_violations,
                build_retry_prompt_fn=_build_retry_prompt,
                budget=role_budget,
                stop_on_same_violations=True,
            )
            response_received_at = datetime.now(UTC).isoformat()
            retries_used = max(retry_result.attempts - 1, 0)
            target_retry_usage[target_id] = target_retry_usage.get(target_id, 0) + retries_used
            output = retry_result.output if isinstance(retry_result.output, dict) else {}
            invocation = {
                "system_request_id": f"sysreq::{uuid.uuid4().hex[:20]}",
                "request_started_at": request_started_at,
                "response_received_at": response_received_at,
                "prompt_digest": _canonical_digest(rendered_prompt),
                "response_digest": _canonical_digest(json.dumps(output, sort_keys=True))
                if output
                else "",
                "transport_status": "ok" if output else "error:empty_output",
                "provider_model": writer_model,
                "transport_backend": "opencode_http",
            }
            if not output:
                role_failures.append(f"{role_name}:transport_failure")
                output = {
                    "target_id": target_id,
                    "status": "abstain",
                    "error": "writer_output_missing",
                }
            if not retry_result.success and retry_result.violations_remaining:
                role_failures.append(f"{role_name}:validation_failed")
                lane_status = target_lanes.setdefault(target_id, {"lane": "publishable"})
                lane_status["lane"] = "diagnostic"
                lane_status["diagnostic_reason"] = "retry_exhausted"
                retry_log.append(
                    {
                        "target_id": target_id,
                        "prompt_id": prompt_id,
                        "role": role_name,
                        "outcome": "retry_exhausted",
                        "remaining_error_violations": retry_result.violations_remaining,
                    }
                )
            role_validation_log.append(
                {
                    "target_id": target_id,
                    "prompt_id": prompt_id,
                    "role": role_name,
                    "attempts": retry_result.attempts,
                    "retries_used": retries_used,
                    "budget": role_budget,
                    "retry_variant": retry_depth_variant,
                    "success": retry_result.success,
                    "budget_exhausted": retry_result.budget_exhausted,
                    "oscillation_detected": retry_result.oscillation_detected,
                    "diminishing_returns": retry_result.diminishing_returns,
                    "violations_remaining": retry_result.violations_remaining,
                    "attempt_entries": role_attempt_entries,
                }
            )
            missing_required = _ensure_required_fields(role_name, output, required)
            role_failures.extend(missing_required)
            output["target_id"] = target_id
            output["prompt_id"] = prompt_id
            output["draft_id"] = draft_id
            role_outputs[role_name] = output
            invocation_rows.append(
                {
                    "target_id": target_id,
                    "target_prompt_id": prompt_id,
                    "writer_role": role_name,
                    "prompt_template_id": prompt_template_id,
                    "prompt_digest": invocation.get(
                        "prompt_digest", _canonical_digest(rendered_prompt)
                    ),
                    "response_digest": invocation.get("response_digest", ""),
                    "system_request_id": invocation.get("system_request_id"),
                    "request_started_at": invocation.get("request_started_at"),
                    "response_received_at": invocation.get("response_received_at"),
                    "transport_status": invocation.get("transport_status", "unknown"),
                    "transport_backend": invocation.get("transport_backend", "opencode_cli"),
                    "provider_model": invocation.get("provider_model", writer_model),
                    "provider_message_id": invocation.get("provider_message_id"),
                    "provider_token_usage": invocation.get("provider_token_usage"),
                    "injected_context": {
                        "convention_spec_tokens": _approx_tokens(
                            json.dumps(convention_spec, sort_keys=True)
                        ),
                        "std_lookup_tokens": _approx_tokens(
                            "\n".join(f"{k} -> {v}" for k, v in std_lookup_payload.items())
                        ),
                        "exemplar_tokens": _approx_tokens(
                            json.dumps(exemplar_payload, sort_keys=True)
                        ),
                        "total_injected_tokens": _approx_tokens(
                            json.dumps(
                                {
                                    "convention_spec": convention_spec,
                                    "std_lookup": std_lookup_payload,
                                    "exemplar_extracts": exemplar_payload,
                                },
                                sort_keys=True,
                            )
                        ),
                        "budget_exceeded": _approx_tokens(
                            json.dumps(
                                {
                                    "convention_spec": convention_spec,
                                    "std_lookup": std_lookup_payload,
                                    "exemplar_extracts": exemplar_payload,
                                },
                                sort_keys=True,
                            )
                        )
                        > total_budget,
                        "section_over_budget": {
                            "convention_spec": _approx_tokens(
                                json.dumps(convention_spec, sort_keys=True)
                            )
                            > convention_budget,
                            "std_lookup": _approx_tokens(
                                "\n".join(f"{k} -> {v}" for k, v in std_lookup_payload.items())
                            )
                            > std_budget,
                            "exemplars": _approx_tokens(
                                json.dumps(exemplar_payload, sort_keys=True)
                            )
                            > exemplar_budget,
                        },
                        "omitted_entries": {
                            "std_lookup": std_omitted,
                            "exemplars": exemplar_omitted,
                        },
                    },
                }
            )

        evidence_output = role_outputs.get("evidence_synthesizer", {})
        amplification_output = role_outputs.get("amplification_author", {})
        example_output = role_outputs.get("example_author", {})
        rationale_output = role_outputs.get("rationale_author", {})
        metadata_output = role_outputs.get("metadata_citation_curator", {})

        writer_evidence_rows.append(evidence_output)
        writer_amplification_rows.append(amplification_output)
        writer_example_rows.append(example_output)
        writer_rationale_rows.append(rationale_output)
        writer_metadata_rows.append(metadata_output)

        lane_status = target_lanes.get(target_id, {"lane": "publishable"})
        is_abstain = (
            bool(target.get("expect_abstain", False))
            or bool(target.get("abstain_expected", False))
            or bool(role_failures)
        )
        draft_status = (
            "diagnostic"
            if lane_status.get("lane") == "diagnostic"
            else ("abstain" if is_abstain else "drafted")
        )
        strength = str(amplification_output.get("normative_strength", "shall")).strip().lower()
        category = "mandatory" if strength == "shall" else "advisory"
        draft_row = {
            "draft_id": draft_id,
            "target_id": target_id,
            "target_prompt_id": prompt_id,
            "corpus": corpus_name,
            "table1_rows": [] if not row_id else [row_id],
            "title": str(metadata_output.get("title", f"Guideline for {prompt_id}")),
            "strength": strength if strength in {"shall", "should"} else "shall",
            "guideline": str(amplification_output.get("guideline_amplification_text", "")),
            "rationale": str(rationale_output.get("rationale_text", "")),
            "enforcement": str(evidence_output.get("mitigation", "")),
            "verification": "Generated from writer-role outputs and judge-gated enforcement.",
            "status": draft_status,
            "evidence_chunk_ids": evidence_ids,
            "evidence_snippets": [str(s.get("text", ""))[:500] for s in snippet_rows[:2]],
            "exemplar_ids_used": selected_exemplars,
            "category": category,
            "exemplar_phrase": ", ".join(selected_exemplars) if selected_exemplars else "none",
            "construct_terms": (
                [str(x) for x in evidence_output.get("construct_scope", [])]
                if isinstance(evidence_output.get("construct_scope"), list)
                else _rust_like_tokens(str(evidence_output.get("construct_scope", "")))
            ),
            "non_compliant_code": str(example_output.get("non_compliant_code", "")),
            "compliant_code": str(example_output.get("compliant_code", "")),
            "example_execution_mode": example_mode,
            "runtime_hazard_target": row_id in runtime_hazard_rows,
            "role_failures": role_failures,
            "lane": lane_status.get("lane", "publishable"),
            "diagnostic_reason": lane_status.get("diagnostic_reason", ""),
        }
        draft_rows.append(draft_row)
        analysis_rows.append(
            {
                "target_prompt_id": prompt_id,
                "reason": "writer_chain_llm",
                "analysis": "Writer chain executed with real LLM role calls and contract-aware prompts.",
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
            "non_abstain_count": len(
                [d for d in draft_rows if str(d.get("status", "")) not in {"abstain", "diagnostic"}]
            ),
            "writer_outputs_complete": True,
        },
    )
    _write_json(
        run_dir / "writer_output_auditor_report.json",
        {
            "run_id": run_id,
            "status": "pass" if not any(d.get("role_failures") for d in draft_rows) else "fail",
            "results": [
                {
                    "draft_id": str(d.get("draft_id", "")),
                    "writer_outputs_complete": not bool(d.get("role_failures")),
                    "evidence_map_valid": bool(d.get("evidence_chunk_ids")),
                    "amplification_specificity_valid": bool(str(d.get("guideline", "")).strip()),
                    "amplification_evidence_linked": bool(d.get("evidence_chunk_ids")),
                    "examples_non_placeholder": str(d.get("status", ""))
                    in {"abstain", "diagnostic"}
                    or "template" not in str(d.get("non_compliant_code", "")).lower(),
                    "rationale_chain_valid": bool(str(d.get("rationale", "")).strip()),
                    "metadata_citation_valid": str(d.get("status", "")) in {"abstain", "diagnostic"}
                    or bool(d.get("table1_rows")),
                    "usage_valid": str(d.get("status", "")) in {"abstain", "diagnostic"}
                    or bool(d.get("exemplar_ids_used")),
                }
                for d in draft_rows
            ],
        },
    )
    total_role_calls = len(role_validation_log)
    total_retries = sum(int(entry.get("retries_used", 0)) for entry in role_validation_log)
    retry_rate = (total_retries / total_role_calls) if total_role_calls else 0.0
    role_validation_report = {
        "run_id": run_id,
        "retry_variant": retry_depth_variant,
        "first_retry_resolution_rate": retry_resolution_rate,
        "convention_retry_budget": convention_retry_budget,
        "per_target_retry_budget": per_target_retry_budget,
        "total_retries": total_retries,
        "total_violations": sum(
            len(attempt.get("violations", []))
            for entry in role_validation_log
            for attempt in (entry.get("attempt_entries") or [])
            if isinstance(attempt, dict)
        ),
        "per_role_retry_counts": {
            role: sum(
                int(entry.get("retries_used", 0))
                for entry in role_validation_log
                if str(entry.get("role", "")) == role
            )
            for role in writer_role_order
        },
        "retry_stats": {
            "total_retries": total_retries,
            "retry_rate": retry_rate,
            "estimated_additional_cost_pct": retry_rate * 100.0,
        },
        "warning_threshold_retry_rate": 0.30,
        "warnings": [
            "retry_rate_above_30pct" if retry_rate > 0.30 else "retry_rate_within_expected_range"
        ],
        "retry_exhausted": retry_log,
        "entries": role_validation_log,
    }
    _write_json(run_dir / "role_validation_report.json", role_validation_report)
    _write_json(
        run_dir / "guideline_manifest.json",
        {
            "run_id": run_id,
            "targets": [
                {
                    "draft_id": str(draft.get("draft_id", "")),
                    "target_id": str(draft.get("target_id", "")),
                    "prompt_id": str(draft.get("target_prompt_id", "")),
                    "status": str(draft.get("status", "drafted")),
                    "lane": str(draft.get("lane", "publishable")),
                    "diagnostic_reason": str(draft.get("diagnostic_reason", "")),
                }
                for draft in draft_rows
            ],
        },
    )

    return execute_validation_and_rendering_pipeline(
        root=root,
        run_dir=run_dir,
        run_id=run_id,
        selected_rows=selected_rows,
        selected=selected,
        mode=mode,
        role_contracts=role_contracts,
        target_prompt_by_id=target_prompt_by_id,
        writer_evidence_rows=writer_evidence_rows,
        writer_example_rows=writer_example_rows,
        writer_rationale_rows=writer_rationale_rows,
        writer_metadata_rows=writer_metadata_rows,
        draft_rows=draft_rows,
        analysis_rows=analysis_rows,
        exemplar_selection_trace=exemplar_selection_trace,
        synthesis_input_trace=synthesis_input_trace,
        guidelines_repo_root=guidelines_repo_root,
        gate_policy_cfg=gate_policy_cfg,
        convention_retry_budget=convention_retry_budget,
        total_retries=total_retries,
        retry_rate=retry_rate,
        retry_depth_variant=retry_depth_variant,
    )
