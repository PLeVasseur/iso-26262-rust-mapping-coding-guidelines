from __future__ import annotations

import json
import os
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opencode_model_registry import configured_default_model, ensure_model_available
from retrieval.writer_host.artifacts import (
    write_evidence_gate_report,
    write_json,
    write_jsonl,
    write_normalization_report,
    write_writer_output_auditor_report,
    write_writer_outputs,
)
from retrieval.writer_host.baseline_guideline_index import load_baseline_guideline_index
from retrieval.writer_host.contracts import (
    ATOM_AUTHOR_ROLES,
    POST_ATOM_ROLES,
    PRE_ATOM_ROLES,
    REQUIRED_ROLES,
    build_contract_snapshot,
    load_contracts,
)
from retrieval.writer_host.editorial_curator import (
    curator_prompt_context,
    normalize_editorial_curation,
    validate_editorial_curation,
)
from retrieval.writer_host.editorial_decomposition import assess_decomposition
from retrieval.writer_host.editorial_metadata import build_editorial_metadata
from retrieval.writer_host.editorial_overlap import analyze_overlap, top_overlap_candidates
from retrieval.writer_host.editorial_planner import (
    candidate_baseline_overlaps,
    flatten_planned_atoms,
    normalize_editorial_plan,
    planner_prompt_context,
    validate_editorial_plan,
)
from retrieval.writer_host.editorial_review_report import build_editorial_review_report
from retrieval.writer_host.editorial_validation import validate_editorial_bundle
from retrieval.writer_host.manifest import load_manifest, target_index
from retrieval.writer_host.retry import run_role_with_retry
from retrieval.writer_host.roles import (
    build_role_prompt,
    extract_claim_map,
    extract_construct_terms,
)
from retrieval.writer_host.validation import (
    canonicalize_metadata_citation_map,
    validate_role_output,
    validate_target_bundle,
)


def _now_run_id() -> str:
    return f"writer_host_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"


def _manifest_evidence_rows(target_row: dict[str, Any]) -> list[dict[str, Any]]:
    selected = target_row.get("selected_evidence") if isinstance(target_row, dict) else []
    if not isinstance(selected, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in selected:
        if not isinstance(item, dict):
            continue
        statement_id = str(item.get("statement_id", "")).strip()
        if not statement_id:
            continue
        rows.append(
            {
                "statement_id": statement_id,
                "raw_statement_id": str(item.get("raw_statement_id", "")).strip(),
                "corpus": str(item.get("corpus", "")).strip(),
                "source_anchor": str(item.get("source_anchor", "")).strip(),
                "final_score": float(item.get("score", 0.0) or 0.0),
                "statement_text": str(item.get("statement_text", "")).strip(),
                "doc_id": str(item.get("doc_id", "")).strip(),
            }
        )
    return rows


def _normalize_synth_output(
    output: dict[str, Any], *, target_id: str
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(output, dict):
        return output, []
    normalized = dict(output)
    changes: list[str] = []
    prompt_id = str(normalized.get("prompt_id", "")).strip()
    if not prompt_id:
        normalized["prompt_id"] = target_id
        prompt_id = target_id
        changes.append("prompt_id_missing_filled")
    elif prompt_id != target_id:
        normalized["prompt_id"] = target_id
        prompt_id = target_id
        changes.append("prompt_id_mismatch_rewritten")
    claim_map = normalized.get("claim_to_evidence_map")
    if isinstance(claim_map, list):
        rewritten: list[Any] = []
        for index, claim in enumerate(claim_map, start=1):
            if not isinstance(claim, dict):
                rewritten.append(claim)
                continue
            entry = dict(claim)
            expected = f"{prompt_id}::claim::{index}"
            if str(entry.get("claim_id", "")).strip() != expected:
                entry["claim_id"] = expected
                if "claim_ids_rewritten" not in changes:
                    changes.append("claim_ids_rewritten")
            rewritten.append(entry)
        normalized["claim_to_evidence_map"] = rewritten
    return normalized, changes


def _normalize_example_output(output: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(output, dict):
        return output, []
    normalized = dict(output)
    changes: list[str] = []
    alias_pairs = (
        ("non_compliant_miri_justification", "non_compliant_miri_skip_justification"),
        ("compliant_miri_justification", "compliant_miri_skip_justification"),
    )
    for alias_key, canonical_key in alias_pairs:
        alias_value = str(normalized.get(alias_key, "")).strip()
        canonical_value = str(normalized.get(canonical_key, "")).strip()
        if alias_value and not canonical_value:
            normalized[canonical_key] = alias_value
            changes.append(f"{canonical_key}_aliased")
    return normalized, changes


def _normalize_author_citation_keys(
    output: dict[str, Any], *, role_name: str, synth_evidence_ids: set[str]
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(output, dict):
        return output, []
    field_name = {
        "amplification_author": "amplification_citation_keys",
        "example_author": "example_citation_keys",
        "rationale_author": "rationale_citation_keys",
    }.get(role_name)
    if not field_name:
        return output, []

    citation_keys = output.get(field_name)
    if not isinstance(citation_keys, list):
        return output, []

    normalized = dict(output)
    rewritten: list[Any] = []
    changes: list[str] = []
    for raw_key in citation_keys:
        key_text = str(raw_key).strip()
        replacement = key_text
        if key_text and key_text not in synth_evidence_ids:
            candidates = []
            for evidence_id in synth_evidence_ids:
                if len(evidence_id) != len(key_text):
                    continue
                if evidence_id.split("::", 1)[0] != key_text.split("::", 1)[0]:
                    continue
                distance = sum(1 for left, right in zip(evidence_id, key_text) if left != right)
                if distance == 1:
                    candidates.append(evidence_id)
            if len(candidates) == 1:
                replacement = candidates[0]
                changes.append(f"{field_name}_evidence_id_typo_corrected")
        rewritten.append(replacement)
    normalized[field_name] = rewritten
    return normalized, changes


def _normalize_metadata_output(
    output: dict[str, Any], *, synth_evidence_ids: set[str]
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(output, dict):
        return output, []
    normalized = dict(output)
    citation_map, inverted = canonicalize_metadata_citation_map(
        normalized.get("citation_key_map"), synth_evidence_ids=synth_evidence_ids
    )
    changes: list[str] = []
    if citation_map:
        normalized["citation_key_map"] = citation_map
    if inverted:
        changes.append("citation_key_map_reversed_inverted")
    bibliography_rows = normalized.get("bibliography_rows")
    if isinstance(bibliography_rows, list):
        deduped: list[Any] = []
        seen_rows: set[tuple[str, str, str]] = set()
        removed = 0
        for row in bibliography_rows:
            if not isinstance(row, dict):
                deduped.append(row)
                continue
            url = str(row.get("url") or row.get("source_anchor") or "").strip()
            title = str(row.get("title") or "").strip()
            author = str(
                row.get("author")
                or row.get("publisher")
                or row.get("document")
                or row.get("corpus")
                or ""
            ).strip()
            key = (url, title, author)
            if url and key in seen_rows:
                removed += 1
                continue
            if url:
                seen_rows.add(key)
            deduped.append(row)
        if removed:
            normalized["bibliography_rows"] = deduped
            changes.append(f"bibliography_rows_exact_duplicates_removed:{removed}")
    return normalized, changes


def _filter_manifest_targets(
    manifest: dict[str, Any], requested_target_ids: set[str]
) -> dict[str, Any]:
    if not requested_target_ids:
        return manifest
    targets = manifest.get("targets") if isinstance(manifest, dict) else []
    if not isinstance(targets, list):
        return manifest
    filtered = [
        row
        for row in targets
        if isinstance(row, dict) and str(row.get("target_id", "")).strip() in requested_target_ids
    ]
    out = dict(manifest)
    out["targets"] = filtered
    return out


def _subset_evidence_rows(
    evidence_rows: list[dict[str, Any]], allowed_ids: list[str]
) -> list[dict[str, Any]]:
    allowed = {str(value).strip() for value in allowed_ids if str(value).strip()}
    if not allowed:
        return list(evidence_rows)
    out = [
        row for row in evidence_rows if str((row or {}).get("statement_id", "")).strip() in allowed
    ]
    return out or list(evidence_rows)


def _write_progress(
    *,
    path: Path,
    run_id: str,
    target_ids: list[str],
    completed_targets: list[str],
    current_target: str,
    current_role: str,
    completed_roles: int,
    total_roles: int,
    status: str,
) -> None:
    write_json(
        path,
        {
            "run_id": run_id,
            "status": status,
            "target_count": len(target_ids),
            "completed_target_count": len(completed_targets),
            "completed_targets": completed_targets,
            "current_target": current_target,
            "current_role": current_role,
            "completed_roles": completed_roles,
            "total_roles": total_roles,
        },
    )


def run(args: Namespace, *, root: Path) -> int:
    run_id = str(getattr(args, "run_id", "") or "").strip() or _now_run_id()
    report_root = str(getattr(args, "report_root", "") or "").strip()
    run_dir = (
        Path(report_root) if report_root else root / ".cache" / "sqlite_kb" / "reports" / run_id
    ).resolve()
    writer_root = run_dir / "writer_subagent_outputs"
    run_dir.mkdir(parents=True, exist_ok=True)
    writer_root.mkdir(parents=True, exist_ok=True)

    contract_path = (
        root / str(getattr(args, "contract_path", "config/s0/writer_prompt_contracts.yaml"))
    ).resolve()
    contracts = load_contracts(contract_path)
    contract_snapshot = build_contract_snapshot(contracts)

    evidence_manifest_raw = str(getattr(args, "evidence_manifest", "") or "").strip()
    if not evidence_manifest_raw:
        raise RuntimeError("writer-run requires --evidence-manifest")
    manifest_path = Path(evidence_manifest_raw).resolve()
    manifest = load_manifest(manifest_path)
    requested_target_ids = {
        str(value).strip()
        for value in list(getattr(args, "target_ids", []) or [])
        if str(value).strip()
    }
    manifest = _filter_manifest_targets(manifest, requested_target_ids)
    manifest_lookup = target_index(manifest)
    target_ids = list(manifest_lookup.keys())
    if not target_ids:
        raise RuntimeError("writer-run evidence manifest missing targets")
    evidence_manifest_path = str(manifest_path)
    progress_path = run_dir / "writer_execution_progress.json"
    total_roles = len(target_ids) * len(REQUIRED_ROLES)

    if bool(getattr(args, "dry_run", False)):
        _write_progress(
            path=progress_path,
            run_id=run_id,
            target_ids=target_ids,
            completed_targets=target_ids,
            current_target="",
            current_role="",
            completed_roles=total_roles,
            total_roles=total_roles,
            status="dry_run",
        )
        write_writer_outputs(
            writer_root=writer_root,
            role_rows={role: [] for role in REQUIRED_ROLES},
            contract_snapshot=contract_snapshot,
            invocation_trace=[],
            merge_validation_report={"run_id": run_id, "status": "pass", "notes": ["dry-run"]},
        )
        write_json(
            run_dir / "writer_host_run_summary.json",
            {
                "run_id": run_id,
                "status": "dry_run",
                "target_ids": target_ids,
                "evidence_manifest": evidence_manifest_path,
                "corpora": list(manifest.get("corpora") or []),
            },
        )
        return 0

    role_cfg = contracts.get("roles") if isinstance(contracts.get("roles"), dict) else {}
    max_retries = int(getattr(args, "max_retries", 2) or 2)
    model = str(getattr(args, "model", "") or os.environ.get("WRITER_MODEL", "")).strip() or None
    effective_model = model or configured_default_model(root / "opencode.json")
    agent = str(getattr(args, "agent", "") or os.environ.get("WRITER_AGENT", "")).strip() or None
    ensure_model_available(effective_model)
    corpora_used = (
        list(manifest.get("corpora") or []) if isinstance(manifest.get("corpora"), list) else []
    )

    role_rows: dict[str, list[dict[str, Any]]] = {role: [] for role in REQUIRED_ROLES}
    invocation_trace: list[dict[str, Any]] = []
    validation_entries: list[dict[str, Any]] = []
    merge_entries: list[dict[str, Any]] = []
    editorial_entries: list[dict[str, Any]] = []
    curation_entries: list[dict[str, Any]] = []
    planned_atoms_rows: list[dict[str, Any]] = []
    drafts: list[dict[str, Any]] = []
    evidence_ids_by_target: dict[str, set[str]] = {}
    completed_targets: list[str] = []
    completed_roles = 0
    baseline_index = load_baseline_guideline_index(root=root)

    _write_progress(
        path=progress_path,
        run_id=run_id,
        target_ids=target_ids,
        completed_targets=completed_targets,
        current_target="",
        current_role="",
        completed_roles=completed_roles,
        total_roles=total_roles,
        status="running",
    )

    for target_id in target_ids:
        manifest_target = manifest_lookup.get(target_id, {})
        if not manifest_target:
            raise RuntimeError(f"target missing from evidence manifest: {target_id}")
        query_text = str(manifest_target.get("query_text", "") or "").strip()
        expected_row_markers = list(manifest_target.get("expected_row_markers") or [])
        expected_row_marker = str(expected_row_markers[0]) if expected_row_markers else ""
        evidence_rows = _manifest_evidence_rows(manifest_target)
        if not query_text:
            raise RuntimeError(f"query_text missing from evidence manifest target: {target_id}")
        if not evidence_rows:
            raise RuntimeError(
                f"selected_evidence missing from evidence manifest target: {target_id}"
            )
        evidence_ids_by_target[target_id] = {
            str(row.get("statement_id", "")) for row in evidence_rows
        }

        prior_outputs: dict[str, dict[str, Any]] = {}

        for role_name in PRE_ATOM_ROLES:
            _write_progress(
                path=progress_path,
                run_id=run_id,
                target_ids=target_ids,
                completed_targets=completed_targets,
                current_target=target_id,
                current_role=role_name,
                completed_roles=completed_roles,
                total_roles=total_roles,
                status="running",
            )
            role_contract = role_cfg.get(role_name) if isinstance(role_cfg, dict) else {}
            if not isinstance(role_contract, dict):
                raise RuntimeError(f"missing role config: {role_name}")
            role_contract_dict: dict[str, Any] = dict(role_contract)

            extra_context: dict[str, Any] = {}
            if role_name == "editorial_planner":
                synth = prior_outputs.get("evidence_synthesizer", {})
                baseline_candidates = candidate_baseline_overlaps(
                    target_id=target_id,
                    query_text=query_text,
                    synth=synth if isinstance(synth, dict) else {},
                    baseline_index=baseline_index,
                )
                extra_context = planner_prompt_context(
                    target_id=target_id,
                    query_text=query_text,
                    synth=synth if isinstance(synth, dict) else {},
                    baseline_candidates=baseline_candidates,
                )

            prompt_text, prompt_hash = build_role_prompt(
                role_name=role_name,
                target_id=target_id,
                prompt_id=target_id,
                table1_row=expected_row_marker,
                query_text=query_text,
                evidence_rows=evidence_rows,
                prior_outputs=prior_outputs,
                role_contract=role_contract_dict,
                run_dir=run_dir,
                extra_context=extra_context,
            )

            def _validate(
                output: dict[str, Any],
                *,
                _role_name: str = role_name,
                _role_contract: dict[str, Any] = role_contract_dict,
                _target_id: str = target_id,
            ) -> list[str]:
                if _role_name == "editorial_planner":
                    return validate_editorial_plan(
                        output=output,
                        evidence_ids=evidence_ids_by_target[_target_id],
                    )
                return validate_role_output(
                    role_name=_role_name,
                    output=output,
                    role_contract=_role_contract,
                    evidence_ids=evidence_ids_by_target[_target_id],
                    expected_prompt_id=_target_id if _role_name == "evidence_synthesizer" else None,
                )

            outcome = run_role_with_retry(
                role_name=role_name,
                prompt=prompt_text,
                validate_output=_validate,
                max_retries=max_retries,
                model=effective_model,
                agent=agent,
            )
            normalization_changes: list[str] = []
            if (
                role_name == "evidence_synthesizer"
                and outcome.failure_kind is None
                and isinstance(outcome.output, dict)
                and outcome.output
            ):
                normalized_output, normalization_changes = _normalize_synth_output(
                    outcome.output, target_id=target_id
                )
                outcome.output = normalized_output
            elif (
                role_name in {"amplification_author", "example_author", "rationale_author"}
                and outcome.failure_kind is None
                and isinstance(outcome.output, dict)
                and outcome.output
            ):
                outcome.output, normalization_changes = _normalize_author_citation_keys(
                    outcome.output,
                    role_name=role_name,
                    synth_evidence_ids=evidence_ids_by_target[target_id],
                )
                if role_name == "example_author":
                    outcome.output, extra_changes = _normalize_example_output(outcome.output)
                    normalization_changes.extend(extra_changes)
            elif (
                role_name == "metadata_citation_curator"
                and outcome.failure_kind is None
                and isinstance(outcome.output, dict)
                and outcome.output
            ):
                outcome.output, normalization_changes = _normalize_metadata_output(
                    outcome.output,
                    synth_evidence_ids=evidence_ids_by_target[target_id],
                )
            if (
                role_name == "editorial_planner"
                and isinstance(outcome.output, dict)
                and outcome.output
            ):
                synth = prior_outputs.get("evidence_synthesizer", {})
                outcome.output = normalize_editorial_plan(
                    target_id=target_id,
                    query_text=query_text,
                    output=outcome.output,
                    synth=synth if isinstance(synth, dict) else {},
                    baseline_candidates=list(
                        extra_context.get("baseline_overlap_candidates") or []
                    ),
                )
                outcome.violations = _validate(outcome.output)
            elif normalization_changes:
                outcome.violations = _validate(outcome.output)
            prior_outputs[role_name] = outcome.output
            role_rows[role_name].append(
                {
                    "target_id": target_id,
                    "plan_id": f"plan::{target_id}" if role_name == "editorial_planner" else "",
                    "atom_id": "",
                    "draft_id": f"draft::{target_id}"
                    if role_name == "evidence_synthesizer"
                    else "",
                    "attempts": outcome.attempts,
                    "status": "pass" if not outcome.violations else "review",
                    "violations": outcome.violations,
                    "output": outcome.output,
                    "prompt_hash": prompt_hash,
                }
            )
            invocation_trace.append(
                {
                    "target_id": target_id,
                    "role": role_name,
                    "session_id": outcome.session_id,
                    "model": effective_model,
                    "agent": agent,
                    "prompt_hash": prompt_hash,
                    "plan_id": f"plan::{target_id}" if role_name == "editorial_planner" else "",
                    "attempts": outcome.attempts,
                    "violations_remaining": outcome.violations,
                    "oscillation_detected": outcome.oscillation_detected,
                    "diminishing_returns": outcome.diminishing_returns,
                    "budget_exhausted": outcome.budget_exhausted,
                    "failure_kind": outcome.failure_kind,
                    "failure_detail": outcome.failure_detail,
                    "normalization_fallback_applied": bool(normalization_changes),
                    "normalization_changes": normalization_changes,
                }
            )
            validation_entries.append(
                {
                    "target_id": target_id,
                    "role": role_name,
                    "plan_id": f"plan::{target_id}" if role_name == "editorial_planner" else "",
                    "attempts": outcome.attempts,
                    "violations": outcome.violations,
                }
            )
            completed_roles += 1
            _write_progress(
                path=progress_path,
                run_id=run_id,
                target_ids=target_ids,
                completed_targets=completed_targets,
                current_target=target_id,
                current_role=role_name,
                completed_roles=completed_roles,
                total_roles=total_roles,
                status="running",
            )

        synth = prior_outputs.get("evidence_synthesizer", {})
        plan_output = prior_outputs.get("editorial_planner", {})
        flattened_atoms = flatten_planned_atoms(
            plan_output if isinstance(plan_output, dict) else {}
        )
        planned_atoms_rows.extend(flattened_atoms)
        writeable_atoms = [
            atom
            for atom in flattened_atoms
            if str(atom.get("disposition", "")).strip() == "write"
            and str(atom.get("write_recommendation", "")).strip() in {"write", "write_with_caution"}
        ]
        if len(writeable_atoms) > 1:
            total_roles += (len(writeable_atoms) - 1) * len(ATOM_AUTHOR_ROLES)

        atom_packages: list[dict[str, Any]] = []
        for atom in writeable_atoms:
            atom_id = str(atom.get("atom_id", "")).strip()
            draft_id = str(atom.get("draft_id", "")).strip() or f"draft::{atom_id}"
            atom_evidence_rows = _subset_evidence_rows(
                evidence_rows, list(atom.get("evidence_ids") or [])
            )
            atom_evidence_ids = {
                str(row.get("statement_id", "")).strip() for row in atom_evidence_rows
            }
            atom_prior_outputs: dict[str, dict[str, Any]] = {
                "evidence_synthesizer": synth if isinstance(synth, dict) else {},
                "editorial_planner": plan_output if isinstance(plan_output, dict) else {},
            }

            for role_name in ATOM_AUTHOR_ROLES:
                _write_progress(
                    path=progress_path,
                    run_id=run_id,
                    target_ids=target_ids,
                    completed_targets=completed_targets,
                    current_target=target_id,
                    current_role=f"{role_name}:{atom_id}",
                    completed_roles=completed_roles,
                    total_roles=total_roles,
                    status="running",
                )
                role_contract = role_cfg.get(role_name) if isinstance(role_cfg, dict) else {}
                if not isinstance(role_contract, dict):
                    raise RuntimeError(f"missing role config: {role_name}")
                role_contract_dict = dict(role_contract)
                prompt_text, prompt_hash = build_role_prompt(
                    role_name=role_name,
                    target_id=target_id,
                    prompt_id=atom_id,
                    table1_row=expected_row_marker,
                    query_text=query_text,
                    evidence_rows=atom_evidence_rows,
                    prior_outputs=atom_prior_outputs,
                    role_contract=role_contract_dict,
                    run_dir=run_dir,
                    extra_context={"planned_atom": atom, "planned_atoms": writeable_atoms},
                )

                def _validate_atom(
                    output: dict[str, Any],
                    *,
                    _role_name: str = role_name,
                    _role_contract: dict[str, Any] = role_contract_dict,
                ) -> list[str]:
                    return validate_role_output(
                        role_name=_role_name,
                        output=output,
                        role_contract=_role_contract,
                        evidence_ids=atom_evidence_ids,
                    )

                outcome = run_role_with_retry(
                    role_name=role_name,
                    prompt=prompt_text,
                    validate_output=_validate_atom,
                    max_retries=max_retries,
                    model=effective_model,
                    agent=agent,
                )
                normalization_changes = []
                if (
                    role_name in {"amplification_author", "example_author", "rationale_author"}
                    and outcome.failure_kind is None
                    and isinstance(outcome.output, dict)
                    and outcome.output
                ):
                    outcome.output, normalization_changes = _normalize_author_citation_keys(
                        outcome.output,
                        role_name=role_name,
                        synth_evidence_ids=atom_evidence_ids,
                    )
                    if role_name == "example_author":
                        outcome.output, extra_changes = _normalize_example_output(outcome.output)
                        normalization_changes.extend(extra_changes)
                elif (
                    role_name == "metadata_citation_curator"
                    and outcome.failure_kind is None
                    and isinstance(outcome.output, dict)
                    and outcome.output
                ):
                    outcome.output, normalization_changes = _normalize_metadata_output(
                        outcome.output,
                        synth_evidence_ids=atom_evidence_ids,
                    )
                if normalization_changes:
                    outcome.violations = _validate_atom(outcome.output)

                atom_prior_outputs[role_name] = outcome.output
                role_rows[role_name].append(
                    {
                        "target_id": target_id,
                        "plan_id": str(plan_output.get("plan_id", f"plan::{target_id}"))
                        if isinstance(plan_output, dict)
                        else f"plan::{target_id}",
                        "atom_id": atom_id,
                        "draft_id": draft_id,
                        "attempts": outcome.attempts,
                        "status": "pass" if not outcome.violations else "review",
                        "violations": outcome.violations,
                        "output": outcome.output,
                        "prompt_hash": prompt_hash,
                    }
                )
                invocation_trace.append(
                    {
                        "target_id": target_id,
                        "plan_id": str(plan_output.get("plan_id", f"plan::{target_id}"))
                        if isinstance(plan_output, dict)
                        else f"plan::{target_id}",
                        "atom_id": atom_id,
                        "draft_id": draft_id,
                        "role": role_name,
                        "session_id": outcome.session_id,
                        "model": effective_model,
                        "agent": agent,
                        "prompt_hash": prompt_hash,
                        "attempts": outcome.attempts,
                        "violations_remaining": outcome.violations,
                        "oscillation_detected": outcome.oscillation_detected,
                        "diminishing_returns": outcome.diminishing_returns,
                        "budget_exhausted": outcome.budget_exhausted,
                        "failure_kind": outcome.failure_kind,
                        "failure_detail": outcome.failure_detail,
                        "normalization_fallback_applied": bool(normalization_changes),
                        "normalization_changes": normalization_changes,
                    }
                )
                validation_entries.append(
                    {
                        "target_id": target_id,
                        "plan_id": str(plan_output.get("plan_id", f"plan::{target_id}"))
                        if isinstance(plan_output, dict)
                        else f"plan::{target_id}",
                        "atom_id": atom_id,
                        "draft_id": draft_id,
                        "role": role_name,
                        "attempts": outcome.attempts,
                        "violations": outcome.violations,
                    }
                )
                completed_roles += 1

            amplification = atom_prior_outputs.get("amplification_author", {})
            rationale = atom_prior_outputs.get("rationale_author", {})
            examples = atom_prior_outputs.get("example_author", {})
            metadata = atom_prior_outputs.get("metadata_citation_curator", {})
            if isinstance(metadata, dict):
                metadata = dict(metadata)
                generated_editorial = build_editorial_metadata(
                    target_id=target_id,
                    query_text=query_text,
                    synth=synth if isinstance(synth, dict) else {},
                    amplification=amplification if isinstance(amplification, dict) else {},
                    rationale=rationale if isinstance(rationale, dict) else {},
                    examples=examples if isinstance(examples, dict) else {},
                    metadata=metadata,
                    evidence_rows=atom_evidence_rows,
                )
                generated_editorial.update(
                    {
                        "proposed_title": str(atom.get("title", "")).strip(),
                        "review_question": str(atom.get("review_question", "")).strip(),
                        "candidate_chapter": str(atom.get("chapter", "expressions")).strip(),
                        "primary_construct_family": str(
                            atom.get("primary_construct_family", "")
                        ).strip(),
                        "secondary_construct_families": list(
                            atom.get("secondary_construct_families") or []
                        ),
                        "published_overlap_hints": list(
                            ((atom.get("baseline_overlap") or {}).get("candidates") or [])
                        ),
                        "sibling_overlap_hints": list(
                            ((atom.get("batch_overlap") or {}).get("candidates") or [])
                        ),
                    }
                )
                metadata["editorial_metadata"] = generated_editorial
                atom_prior_outputs["metadata_citation_curator"] = metadata
            merge_violations = validate_target_bundle(
                target_id=target_id, outputs=atom_prior_outputs
            )
            merge_hard_violations = [
                item
                for item in merge_violations
                if not str(item).startswith("grounding:metadata:evidence_not_in_synth:")
            ]
            merge_warnings = [
                item for item in merge_violations if item not in merge_hard_violations
            ]
            merge_entries.append(
                {
                    "target_id": target_id,
                    "atom_id": atom_id,
                    "draft_id": draft_id,
                    "status": "pass" if not merge_hard_violations else "fail",
                    "violations": merge_hard_violations,
                    "warnings": merge_warnings,
                }
            )
            validation_entries.append(
                {
                    "target_id": target_id,
                    "atom_id": atom_id,
                    "draft_id": draft_id,
                    "role": "merge",
                    "attempts": 1,
                    "violations": merge_hard_violations,
                    "warnings": merge_warnings,
                }
            )
            editorial_metadata = (
                dict(metadata.get("editorial_metadata") or {}) if isinstance(metadata, dict) else {}
            )
            claim_to_evidence_map = [
                row
                for row in extract_claim_map(synth if isinstance(synth, dict) else {})
                if str((row or {}).get("claim_id", "")).strip() in set(atom.get("claim_ids") or [])
            ]
            if not claim_to_evidence_map:
                claim_to_evidence_map = extract_claim_map(synth if isinstance(synth, dict) else {})
            draft = {
                "draft_id": draft_id,
                "target_id": target_id,
                "atom_id": atom_id,
                "source_plan_id": str(plan_output.get("plan_id", f"plan::{target_id}"))
                if isinstance(plan_output, dict)
                else f"plan::{target_id}",
                "target_prompt_id": target_id,
                "status": "drafted",
                "construct_terms": extract_construct_terms(
                    synth if isinstance(synth, dict) else {}
                ),
                "claim_to_evidence_map": claim_to_evidence_map,
                "title": str(atom.get("title", "")).strip(),
                "review_question": str(atom.get("review_question", "")).strip(),
                "chapter": str(atom.get("chapter", "expressions")).strip(),
                "primary_construct_family": str(atom.get("primary_construct_family", "")).strip(),
                "topic_keywords": list(editorial_metadata.get("topic_keywords") or []),
                "planner_decision": str(plan_output.get("decision", ""))
                if isinstance(plan_output, dict)
                else "",
            }
            evidence_quality = {
                "status": str(editorial_metadata.get("evidence_quality_status", "pass")),
                "issues": list(editorial_metadata.get("evidence_quality_issues") or []),
                "blocked": str(editorial_metadata.get("evidence_quality_status", "pass")) == "fail",
            }
            decomposition = assess_decomposition(
                target_id=target_id,
                synth=synth if isinstance(synth, dict) else {},
                amplification=amplification if isinstance(amplification, dict) else {},
                metadata=metadata if isinstance(metadata, dict) else {},
            )
            editorial_violations = validate_editorial_bundle(
                target_id=target_id,
                draft=draft,
                metadata=metadata if isinstance(metadata, dict) else {},
                synth=synth if isinstance(synth, dict) else {},
                evidence_quality=evidence_quality,
                decomposition=decomposition,
            )
            baseline_candidates = top_overlap_candidates(
                {
                    "title": draft["title"],
                    "chapter": draft["chapter"],
                    "construct_terms": draft["construct_terms"],
                    "claim_text_blob": " ".join(
                        str(row.get("claim_text", "")).strip()
                        for row in claim_to_evidence_map
                        if isinstance(row, dict)
                    ),
                    "review_question": draft["review_question"],
                },
                baseline_index,
                candidate_id_key="guideline_id",
            )
            package = {
                "target_id": target_id,
                "plan_id": str(plan_output.get("plan_id", f"plan::{target_id}"))
                if isinstance(plan_output, dict)
                else f"plan::{target_id}",
                "atom_id": atom_id,
                "draft_id": draft_id,
                "draft": draft,
                "amplification": amplification,
                "rationale": rationale,
                "examples": examples,
                "metadata": metadata,
                "evidence_quality": evidence_quality,
                "decomposition": decomposition,
                "editorial_violations": editorial_violations,
                "baseline_overlap_candidates": baseline_candidates,
            }
            atom_packages.append(package)
            editorial_entries.append(
                {
                    "target_id": target_id,
                    "atom_id": atom_id,
                    "draft_id": draft_id,
                    "title": draft["title"],
                    "chapter": draft["chapter"],
                    "construct_terms": draft["construct_terms"],
                    "claim_text_blob": " ".join(
                        str(row.get("claim_text", "")).strip()
                        for row in list(draft.get("claim_to_evidence_map") or [])
                        if isinstance(row, dict)
                    ),
                    "review_question": draft["review_question"],
                    "editorial_violations": editorial_violations,
                    "evidence_quality": evidence_quality,
                    "decomposition": decomposition,
                }
            )
            validation_entries.append(
                {
                    "target_id": target_id,
                    "atom_id": atom_id,
                    "draft_id": draft_id,
                    "role": "editorial",
                    "attempts": 1,
                    "violations": editorial_violations,
                }
            )

        family_overlap = analyze_overlap(
            [
                {
                    "target_id": row.get("target_id", ""),
                    "atom_id": row.get("atom_id", ""),
                    "title": row.get("draft", {}).get("title", ""),
                    "chapter": row.get("draft", {}).get("chapter", ""),
                    "construct_terms": row.get("draft", {}).get("construct_terms", []),
                    "claim_text_blob": " ".join(
                        str(claim.get("claim_text", "")).strip()
                        for claim in list(
                            (row.get("draft", {}) or {}).get("claim_to_evidence_map") or []
                        )
                        if isinstance(claim, dict)
                    ),
                }
                for row in atom_packages
            ]
        )
        baseline_candidates_by_atom = {
            str(row.get("atom_id", "")): list(row.get("baseline_overlap_candidates") or [])
            for row in atom_packages
        }
        role_name = POST_ATOM_ROLES[0]
        role_contract = role_cfg.get(role_name) if isinstance(role_cfg, dict) else {}
        if not isinstance(role_contract, dict):
            raise RuntimeError(f"missing role config: {role_name}")
        role_contract_dict = dict(role_contract)
        prompt_text, prompt_hash = build_role_prompt(
            role_name=role_name,
            target_id=target_id,
            prompt_id=target_id,
            table1_row=expected_row_marker,
            query_text=query_text,
            evidence_rows=evidence_rows,
            prior_outputs={
                "evidence_synthesizer": synth if isinstance(synth, dict) else {},
                "editorial_planner": plan_output if isinstance(plan_output, dict) else {},
            },
            role_contract=role_contract_dict,
            run_dir=run_dir,
            extra_context=curator_prompt_context(
                target_id=target_id,
                planned_atoms=writeable_atoms,
                atom_packages=[
                    {
                        "atom_id": row.get("atom_id", ""),
                        "draft_id": row.get("draft_id", ""),
                        "title": (row.get("draft") or {}).get("title", ""),
                        "review_question": (row.get("draft") or {}).get("review_question", ""),
                        "chapter": (row.get("draft") or {}).get("chapter", ""),
                        "editorial_violations": row.get("editorial_violations", []),
                        "evidence_quality": row.get("evidence_quality", {}),
                    }
                    for row in atom_packages
                ],
                overlap_pairs=list(family_overlap.get("pairs") or []),
                baseline_candidates=baseline_candidates_by_atom,
            ),
        )

        known_draft_ids = {str(row.get("draft_id", "")).strip() for row in atom_packages}
        known_atom_ids = {str(row.get("atom_id", "")).strip() for row in atom_packages}

        def _validate_curator(output: dict[str, Any]) -> list[str]:
            return validate_editorial_curation(
                output=output,
                known_draft_ids=known_draft_ids,
                known_atom_ids=known_atom_ids,
            )

        curator_outcome = run_role_with_retry(
            role_name=role_name,
            prompt=prompt_text,
            validate_output=_validate_curator,
            max_retries=max_retries,
            model=effective_model,
            agent=agent,
        )
        if isinstance(curator_outcome.output, dict) and curator_outcome.output:
            curator_outcome.output = normalize_editorial_curation(
                target_id=target_id,
                output=curator_outcome.output,
                atom_packages=atom_packages,
            )
            curator_outcome.violations = _validate_curator(curator_outcome.output)
        role_rows[role_name].append(
            {
                "target_id": target_id,
                "plan_id": str(plan_output.get("plan_id", f"plan::{target_id}"))
                if isinstance(plan_output, dict)
                else f"plan::{target_id}",
                "atom_id": "",
                "draft_id": "",
                "attempts": curator_outcome.attempts,
                "status": "pass" if not curator_outcome.violations else "review",
                "violations": curator_outcome.violations,
                "output": curator_outcome.output,
                "prompt_hash": prompt_hash,
            }
        )
        invocation_trace.append(
            {
                "target_id": target_id,
                "plan_id": str(plan_output.get("plan_id", f"plan::{target_id}"))
                if isinstance(plan_output, dict)
                else f"plan::{target_id}",
                "role": role_name,
                "session_id": curator_outcome.session_id,
                "model": effective_model,
                "agent": agent,
                "prompt_hash": prompt_hash,
                "attempts": curator_outcome.attempts,
                "violations_remaining": curator_outcome.violations,
                "oscillation_detected": curator_outcome.oscillation_detected,
                "diminishing_returns": curator_outcome.diminishing_returns,
                "budget_exhausted": curator_outcome.budget_exhausted,
                "failure_kind": curator_outcome.failure_kind,
                "failure_detail": curator_outcome.failure_detail,
                "normalization_fallback_applied": False,
                "normalization_changes": [],
            }
        )
        validation_entries.append(
            {
                "target_id": target_id,
                "role": role_name,
                "attempts": curator_outcome.attempts,
                "violations": curator_outcome.violations,
            }
        )
        completed_roles += 1
        curation = curator_outcome.output if isinstance(curator_outcome.output, dict) else {}
        curation_entries.append(curation)
        decisions = {
            str(row.get("atom_id", "")).strip(): row
            for row in list(curation.get("atom_decisions") or [])
            if isinstance(row, dict)
        }
        for package in atom_packages:
            atom_id = str(package.get("atom_id", "")).strip()
            decision = decisions.get(atom_id, {})
            disposition = str(decision.get("disposition", "")).strip()
            export_recommendation = str(decision.get("export_recommendation", "")).strip()
            if (
                disposition not in {"keep", "keep_as_narrower_residue"}
                or export_recommendation != "export"
            ):
                continue
            draft = dict(package.get("draft") or {})
            draft["curation_disposition"] = disposition
            draft["curation_reason"] = str(decision.get("final_why", "")).strip()
            draft["baseline_overlap_status"] = str(
                ((decision.get("baseline_overlap_decision") or {}).get("status", ""))
            ).strip()
            draft["publishability_hint"] = (
                "needs_human_review" if disposition == "keep_as_narrower_residue" else "candidate"
            )
            drafts.append(draft)

        completed_targets.append(target_id)
        _write_progress(
            path=progress_path,
            run_id=run_id,
            target_ids=target_ids,
            completed_targets=completed_targets,
            current_target=target_id,
            current_role="editorial_curator",
            completed_roles=completed_roles,
            total_roles=total_roles,
            status="running",
        )

    write_writer_outputs(
        writer_root=writer_root,
        role_rows=role_rows,
        contract_snapshot=contract_snapshot,
        invocation_trace=invocation_trace,
        merge_validation_report={
            "run_id": run_id,
            "status": "pass"
            if not any(bool(entry.get("violations")) for entry in merge_entries)
            else "fail",
            "target_count": len(target_ids),
            "entries": merge_entries,
        },
    )
    write_json(
        run_dir / "role_validation_report.json", {"run_id": run_id, "entries": validation_entries}
    )
    write_jsonl(run_dir / "planned_atoms.jsonl", planned_atoms_rows)
    write_jsonl(run_dir / "drafts.jsonl", drafts)
    write_json(
        run_dir / "editorial_review_report.json",
        build_editorial_review_report(editorial_entries),
    )
    write_json(
        run_dir / "editorial_curation_report.json",
        {
            "run_id": run_id,
            "target_count": len(target_ids),
            "entries": curation_entries,
        },
    )
    write_normalization_report(
        run_dir / "normalization_report.json",
        run_id=run_id,
        rows=role_rows["evidence_synthesizer"],
    )
    write_evidence_gate_report(
        run_dir / "evidence_synthesizer_gate_report.json",
        run_id=run_id,
        rows=role_rows["evidence_synthesizer"],
        evidence_id_by_target=evidence_ids_by_target,
    )
    gate_report = json.loads(
        (run_dir / "evidence_synthesizer_gate_report.json").read_text(encoding="utf-8")
    )
    write_writer_output_auditor_report(
        run_dir / "writer_output_auditor_report.json",
        run_id=run_id,
        gate_report=gate_report,
    )

    has_violations = any(
        bool(entry.get("violations"))
        for entry in validation_entries
        if str(entry.get("role", "")) != "editorial"
    )

    write_json(
        run_dir / "writer_host_run_summary.json",
        {
            "run_id": run_id,
            "status": "completed" if not has_violations else "completed_with_violations",
            "target_ids": target_ids,
            "run_dir": str(run_dir),
            "normalization_report": str(run_dir / "normalization_report.json"),
            "evidence_gate_report": str(run_dir / "evidence_synthesizer_gate_report.json"),
            "evidence_manifest": evidence_manifest_path,
            "corpora": corpora_used,
            "editorial_review_report": str(run_dir / "editorial_review_report.json"),
            "editorial_curation_report": str(run_dir / "editorial_curation_report.json"),
            "planned_atoms": str(run_dir / "planned_atoms.jsonl"),
        },
    )
    _write_progress(
        path=progress_path,
        run_id=run_id,
        target_ids=target_ids,
        completed_targets=completed_targets,
        current_target="",
        current_role="",
        completed_roles=completed_roles,
        total_roles=total_roles,
        status="completed" if not has_violations else "completed_with_violations",
    )
    return 0 if not has_violations else 2
