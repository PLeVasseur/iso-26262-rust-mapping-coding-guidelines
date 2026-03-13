from __future__ import annotations

from typing import Any


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalize_curator_disposition(value: Any) -> str:
    text = _clean(value).lower()
    mapping = {
        "": "needs_human_review",
        "keep": "keep",
        "keep_as_narrower_residue": "keep_as_narrower_residue",
        "merge_with_batch_atom": "merge_with_batch_atom",
        "drop_batch_duplicate": "drop_batch_duplicate",
        "drop_baseline_overlap": "drop_baseline_overlap",
        "drop_low_distinctness": "drop_low_distinctness",
        "drop_low_evidence_support": "drop_low_evidence_support",
        "needs_human_review": "needs_human_review",
        "abstain": "abstain",
        "drop": "drop_low_evidence_support",
        "do_not_export": "drop_low_evidence_support",
        "review": "needs_human_review",
        "export": "keep",
        "human_review": "needs_human_review",
    }
    return mapping.get(text, text or "needs_human_review")


def _normalize_batch_status(value: Any) -> str:
    text = _clean(value).lower()
    mapping = {
        "": "clear",
        "clear": "clear",
        "keep_both": "keep_both",
        "merge_required": "merge_required",
        "drop_duplicate": "drop_duplicate",
        "uncertain": "uncertain",
        "no_overlap": "clear",
        "no_material_overlap": "clear",
        "none": "clear",
    }
    return mapping.get(text, text or "uncertain")


def _normalize_baseline_status(value: Any) -> str:
    text = _clean(value).lower()
    mapping = {
        "": "clear",
        "clear": "clear",
        "partial_but_keep": "partial_but_keep",
        "drop": "drop",
        "needs_review": "needs_review",
        "no_material_overlap": "clear",
        "no_overlap": "clear",
        "none": "clear",
        "keep": "partial_but_keep",
        "meaningful_residue": "partial_but_keep",
        "not_a_baseline_restatement": "clear",
        "distinct": "clear",
    }
    return mapping.get(text, text or "needs_review")


def curator_prompt_context(
    *,
    target_id: str,
    planned_atoms: list[dict[str, Any]],
    atom_packages: list[dict[str, Any]],
    overlap_pairs: list[dict[str, Any]],
    baseline_candidates: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    return {
        "curator_rules": [
            "Keep the smallest non-overlapping set of supportable atoms.",
            "Drop sibling duplicates unless they ask materially different review questions.",
            "Drop baseline restatements unless meaningful uncovered residue remains.",
            "Do not export off-target or low-evidence atoms by default.",
            "Prefer needs_human_review over over-exporting uncertain atoms.",
        ],
        "planned_atoms": planned_atoms,
        "candidate_drafts": atom_packages,
        "batch_overlap_candidates": overlap_pairs,
        "baseline_overlap_candidates": baseline_candidates,
    }


def validate_editorial_curation(
    *,
    output: dict[str, Any],
    known_draft_ids: set[str],
    known_atom_ids: set[str],
) -> list[str]:
    violations: list[str] = []
    for key in (
        "target_id",
        "family_id",
        "decision_summary",
        "decision_confidence",
        "atom_decisions",
    ):
        if key not in output:
            violations.append(f"missing_required:{key}")
    atom_decisions = output.get("atom_decisions")
    if not isinstance(atom_decisions, list):
        violations.append("atom_decisions_not_list")
        return violations
    seen_atoms: set[str] = set()
    for index, row in enumerate(atom_decisions):
        if not isinstance(row, dict):
            violations.append(f"atom_decision_not_object:{index}")
            continue
        atom_id = _clean(row.get("atom_id"))
        draft_id = _clean(row.get("draft_id"))
        if not atom_id or atom_id not in known_atom_ids:
            violations.append(f"unknown_atom_id:{index}")
        if not draft_id or draft_id not in known_draft_ids:
            violations.append(f"unknown_draft_id:{index}")
        if atom_id in seen_atoms:
            violations.append(f"duplicate_atom_decision:{index}")
        else:
            seen_atoms.add(atom_id)
        disposition = _clean(row.get("disposition"))
        if disposition not in {
            "keep",
            "keep_as_narrower_residue",
            "merge_with_batch_atom",
            "drop_batch_duplicate",
            "drop_baseline_overlap",
            "drop_low_distinctness",
            "drop_low_evidence_support",
            "needs_human_review",
            "abstain",
        }:
            violations.append(f"invalid_disposition:{index}")
        if _clean(row.get("export_recommendation")) not in {
            "export",
            "do_not_export",
            "review_before_export",
        }:
            violations.append(f"invalid_export_recommendation:{index}")
        for block_name, allowed in (
            (
                "batch_overlap_decision",
                {"clear", "keep_both", "merge_required", "drop_duplicate", "uncertain"},
            ),
            ("baseline_overlap_decision", {"clear", "partial_but_keep", "drop", "needs_review"}),
        ):
            block = row.get(block_name)
            if not isinstance(block, dict):
                violations.append(f"{block_name}_not_object:{index}")
                continue
            status = _clean(block.get("status"))
            if status not in allowed:
                violations.append(f"{block_name}_invalid_status:{index}")
    return violations


def normalize_editorial_curation(
    *,
    target_id: str,
    output: dict[str, Any],
    atom_packages: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = dict(output)
    normalized["target_id"] = target_id
    normalized["family_id"] = _clean(output.get("family_id")) or target_id
    normalized["decision_summary"] = (
        _clean(output.get("decision_summary")) or "No explicit curation summary provided."
    )
    normalized["decision_confidence"] = _clean(output.get("decision_confidence")) or "medium"
    decisions = normalized.get("atom_decisions")
    if not isinstance(decisions, list):
        decisions = []
    if not decisions:
        decisions = [
            {
                "atom_id": _clean(row.get("atom_id")),
                "draft_id": _clean(row.get("draft_id")),
                "disposition": "abstain"
                if bool((row.get("evidence_quality") or {}).get("blocked"))
                else "keep",
                "decision_confidence": "low",
                "editorial_quality": {
                    "title_quality": "review",
                    "chapter_quality": "review",
                    "example_alignment": "review",
                    "evidence_quality": "fail"
                    if bool((row.get("evidence_quality") or {}).get("blocked"))
                    else "pass",
                },
                "batch_overlap_decision": {"status": "clear", "compared_atoms": []},
                "baseline_overlap_decision": {"status": "clear", "overlapping_guidelines": []},
                "final_why": "Fallback curation decision.",
                "export_recommendation": "do_not_export"
                if bool((row.get("evidence_quality") or {}).get("blocked"))
                else "export",
            }
            for row in atom_packages
        ]
    normalized_rows: list[dict[str, Any]] = []
    seen_atom_ids: set[str] = set()
    package_by_atom = {str(row.get("atom_id", "")).strip(): row for row in atom_packages}
    for row in decisions:
        if not isinstance(row, dict):
            continue
        entry = dict(row)
        atom_id = _clean(entry.get("atom_id"))
        if not atom_id or atom_id in seen_atom_ids:
            continue
        seen_atom_ids.add(atom_id)
        package = package_by_atom.get(atom_id, {})
        entry["draft_id"] = _clean(entry.get("draft_id")) or _clean(package.get("draft_id"))
        entry["decision_confidence"] = _clean(entry.get("decision_confidence")) or "medium"
        raw_disposition = entry.get("disposition")
        if not _clean(raw_disposition):
            raw_disposition = entry.get("decision")
        entry["disposition"] = _normalize_curator_disposition(raw_disposition)
        entry["editorial_quality"] = dict(entry.get("editorial_quality") or {})
        batch_block = dict(entry.get("batch_overlap_decision") or {})
        batch_block["status"] = _normalize_batch_status(batch_block.get("status"))
        batch_block.setdefault("compared_atoms", [])
        entry["batch_overlap_decision"] = batch_block
        baseline_block = dict(entry.get("baseline_overlap_decision") or {})
        baseline_block["status"] = _normalize_baseline_status(baseline_block.get("status"))
        baseline_block.setdefault("overlapping_guidelines", [])
        entry["baseline_overlap_decision"] = baseline_block
        entry["final_why"] = (
            _clean(entry.get("final_why"))
            or _clean(entry.get("decision_reason"))
            or _clean(entry.get("decision_rationale"))
            or _clean(entry.get("reason"))
            or _clean(entry.get("rationale"))
            or "No curator rationale provided."
        )
        entry["export_recommendation"] = _clean(entry.get("export_recommendation")) or (
            "export"
            if entry["disposition"] in {"keep", "keep_as_narrower_residue"}
            else "do_not_export"
        )
        normalized_rows.append(entry)
    normalized["atom_decisions"] = normalized_rows
    normalized["merge_groups"] = list(normalized.get("merge_groups") or [])
    normalized["dropped_atoms"] = list(normalized.get("dropped_atoms") or [])
    normalized["needs_human_review"] = list(normalized.get("needs_human_review") or [])
    return normalized
