from __future__ import annotations

import re
from typing import Any

from retrieval.writer_host.editorial_overlap import top_overlap_candidates
from retrieval.writer_host.title_policy import build_review_question, derive_title

_CHAPTERS = {
    "attributes",
    "concurrency",
    "exceptions-and-errors",
    "expressions",
    "macros",
    "ownership-and-destruction",
    "patterns",
    "types-and-traits",
    "unsafety",
}

_CHAPTER_ALIASES = {
    "attributes-and-diagnostics": "attributes",
    "diagnostics": "attributes",
    "diagnostic-attributes": "attributes",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text or "candidate"


def _normalize_planner_disposition(value: Any) -> str:
    text = _clean(value).lower()
    mapping = {
        "": "write",
        "write": "write",
        "export": "write",
        "keep": "write",
        "emit": "write",
        "draft": "write",
        "drop": "drop",
        "omit": "drop",
        "abstain": "drop",
        "do_not_export": "drop",
        "needs_review": "needs_review",
        "needs_human_review": "needs_review",
        "review": "needs_review",
    }
    return mapping.get(text, text or "write")


def _normalize_chapter(value: Any) -> str:
    text = _clean(value).lower()
    return _CHAPTER_ALIASES.get(text, text or "expressions")


def planner_prompt_context(
    *,
    target_id: str,
    query_text: str,
    synth: dict[str, Any],
    baseline_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "planner_rules": [
            "Plan the smallest set of guideline atoms that each correspond to one review question.",
            "Split only when hazards, construct families, chapter homes, or example shapes differ materially.",
            "If the evidence is off-target or negative-control style, abstain rather than inventing a guideline.",
            "If the best candidate is already covered by the baseline with no meaningful residue, drop it.",
        ],
        "baseline_overlap_candidates": baseline_candidates,
        "planning_schema_hint": {
            "target_id": target_id,
            "decision": "emit_one|split|abstain_off_target|drop_as_baseline_overlap|needs_human_review",
            "rule_atoms": [
                {
                    "atom_id": f"{target_id}::atom::example",
                    "title": derive_title(
                        target_id=target_id, synth=synth, amplification={}, metadata={}
                    ),
                    "review_question": build_review_question(
                        title=derive_title(
                            target_id=target_id, synth=synth, amplification={}, metadata={}
                        ),
                        chapter="expressions",
                    ),
                    "chapter": "expressions",
                    "primary_construct_family": "subset",
                    "evidence_ids": list(synth.get("evidence_ids") or []),
                    "claim_ids": [
                        str(row.get("claim_id", ""))
                        for row in list(synth.get("claim_to_evidence_map") or [])
                        if isinstance(row, dict)
                    ],
                }
            ],
            "query_text": query_text,
        },
    }


def validate_editorial_plan(*, output: dict[str, Any], evidence_ids: set[str]) -> list[str]:
    violations: list[str] = []
    required = ("target_id", "decision", "decision_confidence", "decision_rationale", "rule_atoms")
    for key in required:
        if key not in output:
            violations.append(f"missing_required:{key}")
    rule_atoms = output.get("rule_atoms")
    if not isinstance(rule_atoms, list):
        violations.append("rule_atoms_not_list")
        return violations
    seen_atom_ids: set[str] = set()
    writeable = 0
    for index, atom in enumerate(rule_atoms):
        if not isinstance(atom, dict):
            violations.append(f"atom_not_object:{index}")
            continue
        atom_id = _clean(atom.get("atom_id"))
        if not atom_id:
            violations.append(f"atom_missing_id:{index}")
        elif atom_id in seen_atom_ids:
            violations.append(f"atom_duplicate_id:{index}")
        else:
            seen_atom_ids.add(atom_id)
        disposition = _clean(atom.get("disposition"))
        if disposition not in {"write", "drop", "needs_review"}:
            violations.append(f"atom_invalid_disposition:{index}")
        if disposition == "write":
            writeable += 1
            for key in (
                "title",
                "review_question",
                "chapter",
                "primary_construct_family",
                "hazard_focus",
                "mechanism_focus",
                "mitigation_focus",
                "why_distinct",
                "writer_brief",
            ):
                if not _clean(atom.get(key)):
                    violations.append(f"atom_missing_{key}:{index}")
            chapter = _clean(atom.get("chapter"))
            if chapter not in _CHAPTERS:
                violations.append(f"atom_invalid_chapter:{index}")
            atom_evidence_ids = atom.get("evidence_ids")
            if not isinstance(atom_evidence_ids, list) or not atom_evidence_ids:
                violations.append(f"atom_evidence_ids_missing:{index}")
            else:
                for ref_index, evidence_id in enumerate(atom_evidence_ids):
                    evidence_text = _clean(evidence_id)
                    if not evidence_text:
                        violations.append(f"atom_evidence_id_blank:{index}:{ref_index}")
                    elif evidence_ids and evidence_text not in evidence_ids:
                        violations.append(f"atom_unknown_evidence_id:{index}:{ref_index}")
            claim_ids = atom.get("claim_ids")
            if not isinstance(claim_ids, list) or not claim_ids:
                violations.append(f"atom_claim_ids_missing:{index}")
            for block_name in ("batch_overlap", "baseline_overlap"):
                block = atom.get(block_name)
                if not isinstance(block, dict):
                    violations.append(f"atom_{block_name}_not_object:{index}")
                    continue
                status = _clean(block.get("status"))
                if status not in {
                    "none",
                    "low",
                    "partial",
                    "substantial",
                    "near_duplicate",
                    "uncertain",
                }:
                    violations.append(f"atom_{block_name}_invalid_status:{index}")
                candidates = block.get("candidates")
                if not isinstance(candidates, list):
                    violations.append(f"atom_{block_name}_candidates_not_list:{index}")
        recommendation = _clean(atom.get("write_recommendation"))
        if recommendation and recommendation not in {
            "write",
            "write_with_caution",
            "drop_as_batch_duplicate",
            "drop_as_baseline_overlap",
            "needs_human_review",
        }:
            violations.append(f"atom_invalid_write_recommendation:{index}")
    decision = _clean(output.get("decision"))
    if decision not in {
        "emit_one",
        "split",
        "abstain_off_target",
        "drop_as_baseline_overlap",
        "needs_human_review",
    }:
        violations.append("decision_invalid")
    if decision in {"abstain_off_target", "drop_as_baseline_overlap"} and writeable:
        violations.append("decision_conflicts_with_writeable_atoms")
    return violations


def normalize_editorial_plan(
    *,
    target_id: str,
    query_text: str,
    output: dict[str, Any],
    synth: dict[str, Any],
    baseline_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = dict(output)
    normalized["target_id"] = target_id
    normalized["plan_id"] = _clean(output.get("plan_id")) or f"plan::{target_id}"
    normalized["query_text"] = query_text
    atoms = normalized.get("rule_atoms")
    if not isinstance(atoms, list):
        atoms = []
    claim_rows = [
        row for row in list(synth.get("claim_to_evidence_map") or []) if isinstance(row, dict)
    ]
    default_title = derive_title(target_id=target_id, synth=synth, amplification={}, metadata={})
    if not atoms and _clean(normalized.get("decision")) in {"emit_one", "split", ""}:
        atoms = [
            {
                "atom_id": f"{target_id}::atom::{_slug(default_title)}",
                "disposition": "write",
                "title": default_title,
                "review_question": build_review_question(
                    title=default_title, chapter="expressions"
                ),
                "chapter": "expressions",
                "primary_construct_family": "subset",
                "secondary_construct_families": [],
                "hazard_focus": _clean(synth.get("hazard")),
                "mechanism_focus": _clean(synth.get("mechanism")),
                "mitigation_focus": _clean(synth.get("mitigation")),
                "why_distinct": "Fallback planner atom based on the full evidence synthesis.",
                "evidence_ids": list(synth.get("evidence_ids") or []),
                "claim_ids": [
                    str(row.get("claim_id", "")).strip()
                    for row in claim_rows
                    if _clean(row.get("claim_id"))
                ],
                "writer_brief": "Keep this atom narrow and aligned to one review question.",
                "batch_overlap": {"status": "none", "candidates": []},
                "baseline_overlap": {"status": "low", "candidates": baseline_candidates[:3]},
                "write_recommendation": "write_with_caution",
            }
        ]
        normalized["decision"] = "emit_one"
        normalized.setdefault("decision_confidence", "low")
        normalized.setdefault(
            "decision_rationale",
            "Fallback planner atom emitted because no explicit atoms were returned.",
        )
    normalized_atoms: list[dict[str, Any]] = []
    for index, atom in enumerate(atoms, start=1):
        if not isinstance(atom, dict):
            continue
        entry = dict(atom)
        title = _clean(entry.get("title")) or default_title
        atom_id = _clean(entry.get("atom_id")) or f"{target_id}::atom::{_slug(title or str(index))}"
        entry["atom_id"] = atom_id
        entry["title"] = title
        entry["review_question"] = _clean(entry.get("review_question")) or build_review_question(
            title=title,
            chapter=_clean(entry.get("chapter")) or "expressions",
        )
        entry["disposition"] = _normalize_planner_disposition(entry.get("disposition"))
        entry["chapter"] = _normalize_chapter(entry.get("chapter"))
        entry["primary_construct_family"] = (
            _clean(entry.get("primary_construct_family")) or "subset"
        )
        entry["secondary_construct_families"] = [
            _clean(value)
            for value in list(entry.get("secondary_construct_families") or [])
            if _clean(value)
        ]
        entry["hazard_focus"] = _clean(entry.get("hazard_focus")) or _clean(synth.get("hazard"))
        entry["mechanism_focus"] = _clean(entry.get("mechanism_focus")) or _clean(
            synth.get("mechanism")
        )
        entry["mitigation_focus"] = _clean(entry.get("mitigation_focus")) or _clean(
            synth.get("mitigation")
        )
        entry["why_distinct"] = (
            _clean(entry.get("why_distinct")) or "Planner-proposed candidate atom."
        )
        evidence_list = [
            _clean(value) for value in list(entry.get("evidence_ids") or []) if _clean(value)
        ] or [_clean(value) for value in list(synth.get("evidence_ids") or []) if _clean(value)]
        entry["evidence_ids"] = evidence_list
        claim_ids = [_clean(value) for value in list(entry.get("claim_ids") or []) if _clean(value)]
        if not claim_ids:
            claim_ids = [
                _clean(row.get("claim_id")) for row in claim_rows if _clean(row.get("claim_id"))
            ]
        entry["claim_ids"] = claim_ids
        entry["writer_brief"] = (
            _clean(entry.get("writer_brief"))
            or "Keep the written guideline atom narrow and self-contained."
        )
        entry["required_constructs"] = [
            _clean(value) for value in list(entry.get("required_constructs") or []) if _clean(value)
        ]
        entry["forbidden_constructs"] = [
            _clean(value)
            for value in list(entry.get("forbidden_constructs") or [])
            if _clean(value)
        ]
        entry["example_expectation"] = _clean(entry.get("example_expectation"))
        for block_name in ("batch_overlap", "baseline_overlap"):
            block = entry.get(block_name)
            if not isinstance(block, dict):
                block = {"status": "none", "candidates": []}
            block.setdefault("status", "none")
            block.setdefault("candidates", [])
            entry[block_name] = block
        entry["write_recommendation"] = _clean(entry.get("write_recommendation")) or (
            "write" if entry["disposition"] == "write" else "needs_human_review"
        )
        normalized_atoms.append(entry)
    normalized["rule_atoms"] = normalized_atoms
    return normalized


def flatten_planned_atoms(plan: dict[str, Any]) -> list[dict[str, Any]]:
    plan_id = _clean(plan.get("plan_id"))
    target_id = _clean(plan.get("target_id"))
    decision = _clean(plan.get("decision"))
    rows: list[dict[str, Any]] = []
    for atom in list(plan.get("rule_atoms") or []):
        if not isinstance(atom, dict):
            continue
        atom_id = _clean(atom.get("atom_id"))
        if not atom_id:
            continue
        rows.append(
            {
                "target_id": target_id,
                "plan_id": plan_id,
                "atom_id": atom_id,
                "draft_id": f"draft::{atom_id}",
                "planner_decision": decision,
                **atom,
            }
        )
    return rows


def candidate_baseline_overlaps(
    *,
    target_id: str,
    query_text: str,
    synth: dict[str, Any],
    baseline_index: list[dict[str, Any]],
    top_k: int = 5,
) -> list[dict[str, Any]]:
    draft_like = {
        "target_id": target_id,
        "title": derive_title(target_id=target_id, synth=synth, amplification={}, metadata={}),
        "chapter": "",
        "construct_terms": list(synth.get("construct_scope") or []),
        "claim_text_blob": " ".join(
            _clean(row.get("claim_text"))
            for row in list(synth.get("claim_to_evidence_map") or [])
            if isinstance(row, dict)
        )
        + f" {query_text}",
    }
    return top_overlap_candidates(
        draft_like, baseline_index, top_k=top_k, candidate_id_key="guideline_id"
    )
