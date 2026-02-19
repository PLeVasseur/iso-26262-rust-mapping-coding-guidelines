from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from _common import read_yaml, run_command, write_yaml


def _target_from_seed(seed: dict[str, Any]) -> str:
    anchor = str(seed.get("citation_anchor_id") or "").strip()
    if anchor:
        return anchor
    chunk = str(seed.get("chunk_id") or "").strip()
    if chunk:
        return chunk
    return str(seed.get("seed_id") or "")


def _obligation_from_seed(seed: dict[str, Any]) -> str:
    value = str(seed.get("obligation_unit_id") or "").strip()
    if value:
        return value
    row_key = str(seed.get("row_key") or "").strip()
    if row_key:
        return row_key
    return _target_from_seed(seed)


def _load_seed_index(root: Path) -> dict[str, Any]:
    payload = read_yaml(root / "data/seed_topics.yaml") or {}
    seeds = payload.get("seed_topics") or []

    by_seed: dict[str, dict[str, Any]] = {}
    by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_obligation: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for seed in seeds:
        seed_id = str(seed.get("seed_id") or "").strip()
        if not seed_id:
            continue
        by_seed[seed_id] = seed
        target = _target_from_seed(seed)
        obligation = _obligation_from_seed(seed)
        by_target[target].append(seed)
        by_obligation[obligation].append(seed)

    return {
        "by_seed": by_seed,
        "by_target": by_target,
        "by_obligation": by_obligation,
    }


def _guidelines_for_target(root: Path) -> dict[str, set[str]]:
    path = root / "data/coverage_matrix.csv"
    mapping: dict[str, set[str]] = defaultdict(set)
    if not path.exists():
        return mapping
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            target_id = str(row.get("target_id") or "").strip()
            guideline_id = str(row.get("guideline_id") or "").strip()
            if target_id and guideline_id:
                mapping[target_id].add(guideline_id)
    return mapping


def _load_todo_guidelines(root: Path) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    path = root / "data/todo_guidelines.yaml"
    payload = read_yaml(path) or {}
    guidelines = payload.get("guidelines") or []
    return path, payload, guidelines


def _write_todo_guidelines(
    path: Path, payload: dict[str, Any], guidelines: list[dict[str, Any]]
) -> None:
    payload["guidelines"] = sorted(guidelines, key=lambda item: str(item.get("id") or ""))
    write_yaml(path, payload)


def _load_fls_candidates(root: Path) -> dict[str, dict[str, list[str]]]:
    path = root / "data/fls_target_candidates.yaml"
    if not path.exists():
        return {"by_target": {}, "by_obligation": {}}
    payload = read_yaml(path) or {}
    by_target: dict[str, list[str]] = {}
    by_obligation: dict[str, list[str]] = {}

    for entry in payload.get("target_candidates", []):
        refs = []
        for candidate in entry.get("candidate_fls_refs", []):
            ref = str(candidate.get("fls_ref") or "").strip()
            if ref and ref not in refs:
                refs.append(ref)
        target_id = str(entry.get("target_id") or "").strip()
        obligation = str(entry.get("obligation_unit_id") or "").strip()
        if target_id and refs:
            by_target[target_id] = refs
        if obligation and refs:
            by_obligation[obligation] = refs
    return {"by_target": by_target, "by_obligation": by_obligation}


def _candidate_id(prefix: str, values: list[str]) -> str:
    material = "|".join(values)
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}:{digest}"


def generate_candidates(observation: dict[str, Any], beam_width: int) -> list[dict[str, Any]]:
    deficits = observation.get("deficits") or []
    candidates: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()

    for deficit in deficits:
        deficit_type = str(deficit.get("type") or "")
        guideline_id = str(deficit.get("guideline_id") or "").strip()
        target_id = str(deficit.get("target_id") or "").strip()
        obligation_unit_id = str(deficit.get("obligation_unit_id") or "").strip()

        action: dict[str, Any] | None = None
        rationale = ""

        if deficit_type in {"iso_obligation_gap", "target_fanout_gap"}:
            action = {
                "type": "spawn_rule_for_obligation_unit",
                "target_id": target_id,
                "obligation_unit_id": obligation_unit_id,
            }
            rationale = "expand guideline decomposition coverage"
        elif deficit_type in {"fls_span_gap", "fls_chapter_gap"}:
            action = {
                "type": "assign_missing_fls_refs",
                "guideline_id": guideline_id,
                "target_id": target_id,
                "max_refs": 3,
            }
            rationale = "improve FLS proxy span coverage"
        elif deficit_type in {"quality_gap", "placeholder_gap"} and guideline_id:
            action = {
                "type": "rewrite_rule_statement_specific",
                "guideline_id": guideline_id,
            }
            rationale = "replace generic guideline prose with specific rule language"
        elif deficit_type == "example_gap" and guideline_id:
            action = {
                "type": "rewrite_rule_statement_specific",
                "guideline_id": guideline_id,
            }
            rationale = "repair example narrative and rule-specific guidance"

        if action is None:
            continue

        signature = json.dumps(action, sort_keys=True)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)

        candidate = {
            "candidate_id": _candidate_id("cand", [signature, rationale]),
            "actions": [action],
            "rationale": rationale,
            "source_deficit_id": str(deficit.get("deficit_id") or ""),
        }
        candidates.append(candidate)
        if len(candidates) >= max(1, beam_width):
            break

    return candidates


def _replace_rule_id(value: str, old_rule_id: str, new_rule_id: str) -> str:
    return value.replace(old_rule_id, new_rule_id)


def _next_child_guideline_id(
    parent_id: str, obligation_unit_id: str, existing_ids: set[str]
) -> str:
    ordinal = 1
    while True:
        material = f"{parent_id}|{obligation_unit_id}|{ordinal}"
        digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:12].upper()
        candidate = f"RG-{digest}"
        if candidate not in existing_ids:
            return candidate
        ordinal += 1


def _rule_family_id_for_target(target_id: str) -> str:
    digest = hashlib.sha1(target_id.encode("utf-8")).hexdigest()[:12].upper()
    return f"FAM-{digest}"


def _sanitize_text(value: str) -> str:
    updated = value
    updated = re.sub(r"\bplaceholder\b", "illustrative", updated, flags=re.IGNORECASE)
    updated = re.sub(r"\bpending\b", "ready-for-review", updated, flags=re.IGNORECASE)
    updated = re.sub(r"\btodo\b", "review item", updated, flags=re.IGNORECASE)
    return updated


def _focus_phrase(guideline: dict[str, Any]) -> str:
    refs = [str(item).lower() for item in guideline.get("fls_refs", [])]
    if any("unsafety" in ref for ref in refs):
        return "unsafe blocks and invariants"
    if any("concurrency" in ref for ref in refs):
        return "shared-state and concurrency behavior"
    if any("exceptions_and_errors" in ref for ref in refs):
        return "error propagation and failure handling"
    if any("types_and_traits" in ref for ref in refs):
        return "type modeling and trait constraints"
    if any("macros" in ref for ref in refs):
        return "macro expansion boundaries"
    return "safety-critical Rust control flow"


def _rewrite_example_markdown(path: Path, guideline_id: str, side: str, focus: str) -> None:
    title = f"{side.replace('_', ' ').title()} Example: {guideline_id}"
    if path.exists():
        original = path.read_text(encoding="utf-8")
        sanitized = _sanitize_text(original)
        if sanitized != original:
            path.write_text(sanitized, encoding="utf-8")
            return

    if side == "compliant":
        body = (
            f"This example demonstrates {focus} with explicit, reviewable safety constraints.\n\n"
            "```rust\n"
            "fn main() {\n"
            "    let validated_value: u32 = 42;\n"
            "    let _ = validated_value;\n"
            "}\n"
            "```\n"
        )
    else:
        body = (
            "This example violates the "
            f"{focus} constraints and should be treated as negative evidence.\n\n"
            "```rust\n"
            "fn main() {\n"
            "    let unchecked: i32 = -1;\n"
            "    let _ = unchecked;\n"
            "}\n"
            "```\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{body}", encoding="utf-8")


def apply_assign_missing_fls_refs(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    guideline_filter = str(action.get("guideline_id") or "").strip()
    target_filter = str(action.get("target_id") or "").strip()
    max_refs = int(action.get("max_refs") or 3)

    path, payload, guidelines = _load_todo_guidelines(root)
    seed_index = _load_seed_index(root)
    fls_index = _load_fls_candidates(root)

    changed = 0
    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if guideline_filter and guideline_id != guideline_filter:
            continue

        candidate_refs: list[str] = []
        obligations = [
            str(item).strip() for item in guideline.get("obligation_units", []) if str(item).strip()
        ]
        for obligation in obligations:
            for ref in fls_index["by_obligation"].get(obligation, []):
                if ref not in candidate_refs:
                    candidate_refs.append(ref)

        target_ids: list[str] = []
        for seed_id in guideline.get("iso_seeds", []) or []:
            seed = seed_index["by_seed"].get(str(seed_id).strip())
            if seed is None:
                continue
            target = _target_from_seed(seed)
            if target not in target_ids:
                target_ids.append(target)
        for target in target_ids:
            if target_filter and target != target_filter:
                continue
            for ref in fls_index["by_target"].get(target, []):
                if ref not in candidate_refs:
                    candidate_refs.append(ref)

        if target_filter and not any(target == target_filter for target in target_ids):
            continue

        existing_refs = [
            str(item).strip() for item in guideline.get("fls_refs", []) if str(item).strip()
        ]
        updated_refs = list(existing_refs)
        for ref in candidate_refs:
            if ref in updated_refs:
                continue
            updated_refs.append(ref)
            if len(updated_refs) >= max_refs:
                break

        if updated_refs != existing_refs and updated_refs:
            guideline["fls_refs"] = updated_refs
            changed += 1

    if changed > 0:
        _write_todo_guidelines(path, payload, guidelines)
    return {"changed": changed > 0, "changed_guidelines": changed}


def apply_spawn_rule_for_obligation_unit(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    target_filter = str(action.get("target_id") or "").strip()
    obligation_filter = str(action.get("obligation_unit_id") or "").strip()

    path, payload, guidelines = _load_todo_guidelines(root)
    seed_index = _load_seed_index(root)
    fls_index = _load_fls_candidates(root)
    target_guidelines = _guidelines_for_target(root)

    candidate_seeds: list[dict[str, Any]] = []
    if obligation_filter:
        candidate_seeds.extend(seed_index["by_obligation"].get(obligation_filter, []))
    if target_filter:
        candidate_seeds.extend(seed_index["by_target"].get(target_filter, []))
    if not candidate_seeds:
        return {"changed": False, "reason": "no matching seeds"}

    candidate_seeds = sorted(
        {str(seed.get("seed_id") or ""): seed for seed in candidate_seeds}.values(),
        key=lambda seed: str(seed.get("seed_id") or ""),
    )
    seed = candidate_seeds[0]
    seed_id = str(seed.get("seed_id") or "").strip()
    target_id = target_filter or _target_from_seed(seed)
    obligation = obligation_filter or _obligation_from_seed(seed)

    guidelines_by_id = {
        str(guideline.get("id") or "").strip(): guideline
        for guideline in guidelines
        if str(guideline.get("id") or "").strip()
    }

    parent_id = ""
    for guideline in guidelines:
        gids = [str(item).strip() for item in guideline.get("iso_seeds", []) if str(item).strip()]
        if seed_id in gids:
            parent_id = str(guideline.get("id") or "").strip()
            break

    if not parent_id:
        candidate_parent_ids = sorted(target_guidelines.get(target_id, set()))
        if candidate_parent_ids:
            parent_id = candidate_parent_ids[0]

    if not parent_id or parent_id not in guidelines_by_id:
        return {"changed": False, "reason": "no parent guideline for target"}

    existing_ids = set(guidelines_by_id.keys())
    new_id = _next_child_guideline_id(parent_id, obligation, existing_ids)
    parent = guidelines_by_id[parent_id]
    child = copy.deepcopy(parent)
    child["id"] = new_id
    child["iso_seeds"] = [seed_id]
    child["obligation_units"] = [obligation]
    child["decomposition_parent"] = parent_id
    child["rule_family_id"] = str(
        child.get("rule_family_id") or _rule_family_id_for_target(target_id)
    )

    topic = str(child.get("technical_topic") or "safety topic").lower()
    child["rule_statement"] = (
        f"For obligation unit {obligation}, apply explicit Rust constraints for {topic} "
        "with deterministic verification criteria."
    )
    child["amplification"] = (
        f"This decomposed sub-guideline narrows coverage to obligation {obligation}. "
        "Define allowed patterns, forbidden patterns, and objective pass/fail evidence."
    )
    child["rationale"] = (
        f"Decomposition from {parent_id} increases traceability and enforceability for {target_id}."
    )
    child["state"] = "DRAFT"

    if not child.get("fls_refs"):
        refs = fls_index["by_obligation"].get(obligation, []) or fls_index["by_target"].get(
            target_id, []
        )
        if refs:
            child["fls_refs"] = refs[:3]

    examples = child.get("examples") or {}
    for side in ["compliant", "non_compliant"]:
        entry = examples.get(side) or {}
        code_path = str(entry.get("code_path") or "")
        doc_path = str(entry.get("doc_path") or "")
        if code_path:
            entry["code_path"] = _replace_rule_id(code_path, parent_id, new_id)
        if doc_path:
            entry["doc_path"] = _replace_rule_id(doc_path, parent_id, new_id)
        entry["explanation"] = (
            f"{side.replace('_', ' ').title()} evidence for decomposed rule {new_id} "
            f"covering obligation {obligation}."
        )
        examples[side] = entry
    child["examples"] = examples

    child["evidence_artifacts"] = [
        _replace_rule_id(str(item), parent_id, new_id)
        for item in (child.get("evidence_artifacts") or [])
    ]

    guidelines.append(child)
    _write_todo_guidelines(path, payload, guidelines)
    return {"changed": True, "new_guideline_id": new_id, "parent_guideline_id": parent_id}


def apply_rewrite_rule_statement_specific(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    guideline_filter = str(action.get("guideline_id") or "").strip()
    if not guideline_filter:
        return {"changed": False, "reason": "guideline_id required"}

    path, payload, guidelines = _load_todo_guidelines(root)
    changed = False

    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if guideline_id != guideline_filter:
            continue

        focus = _focus_phrase(guideline)
        scope = str(guideline.get("scope") or "crate")
        topic = str(guideline.get("technical_topic") or "safety")
        guideline["rule_statement"] = (
            f"At {scope} scope, constrain {focus} to approved patterns and reject ambiguous "
            "or implicit behavior in safety-critical Rust code."
        )
        guideline["amplification"] = (
            f"Apply this rule to {topic} decisions with explicit preconditions, invariant checks, "
            "and objective review evidence. Document permitted and forbidden coding patterns."
        )
        guideline["exceptions"] = (
            "Exceptions require documented hazard impact analysis, mitigation evidence, and "
            "independent reviewer approval before merge."
        )
        guideline["rationale"] = (
            f"Constraining {focus} reduces latent faults and improves deterministic behavior "
            "needed for ISO 26262 compliance evidence."
        )
        guideline["decidability_rationale"] = _sanitize_text(
            str(guideline.get("decidability_rationale") or "")
        )

        examples = guideline.get("examples") or {}
        for side in ["compliant", "non_compliant"]:
            entry = examples.get(side) or {}
            doc_path = str(entry.get("doc_path") or "").strip()
            if doc_path:
                _rewrite_example_markdown(root / doc_path, guideline_id, side, focus)
        changed = True
        break

    if changed:
        _write_todo_guidelines(path, payload, guidelines)
    return {"changed": changed, "guideline_id": guideline_filter}


def apply_action(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    action_type = str(action.get("type") or "").strip()
    if action_type == "assign_missing_fls_refs":
        return apply_assign_missing_fls_refs(root, action)
    if action_type == "spawn_rule_for_obligation_unit":
        return apply_spawn_rule_for_obligation_unit(root, action)
    if action_type == "rewrite_rule_statement_specific":
        return apply_rewrite_rule_statement_specific(root, action)
    return {"changed": False, "reason": f"unknown action type {action_type}"}


def refresh_generated_artifacts(root: Path) -> tuple[bool, list[dict[str, Any]]]:
    steps = [
        [sys.executable, "scripts/decompose_guidelines.py"],
        [sys.executable, "scripts/generate_guideline_artifacts.py"],
        [sys.executable, "scripts/scaffold_guideline_fixtures.py"],
    ]
    reports: list[dict[str, Any]] = []
    for command in steps:
        completed = run_command(command, cwd=root)
        reports.append(
            {
                "command": " ".join(command),
                "return_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
        if completed.returncode != 0:
            return False, reports
    return True, reports


def apply_candidate(root: Path, candidate: dict[str, Any]) -> dict[str, Any]:
    action_reports = []
    changed_any = False
    for action in candidate.get("actions", []):
        report = apply_action(root, action)
        action_reports.append({"action": action, "result": report})
        changed_any = changed_any or bool(report.get("changed"))

    if not changed_any:
        return {
            "ok": True,
            "changed": False,
            "action_reports": action_reports,
            "refresh_reports": [],
            "note": "candidate produced no changes",
        }

    refresh_ok, refresh_reports = refresh_generated_artifacts(root)
    return {
        "ok": refresh_ok,
        "changed": True,
        "action_reports": action_reports,
        "refresh_reports": refresh_reports,
    }
