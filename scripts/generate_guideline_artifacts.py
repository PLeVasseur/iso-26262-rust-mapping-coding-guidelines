#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

from _common import (
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    load_guidelines_payload,
    read_yaml,
    repo_root,
    write_yaml,
)
from _fls_proxy import (
    guideline_id_for_scaffold,
    normalize_obligation_unit,
    rule_family_id_for_target,
)

DEFAULT_GUIDELINE_CATEGORY = "Required"
DEFAULT_SCOPE = "crate"
DEFAULT_DEVIATION_REQUIREMENTS = (
    "Document deviation rationale, impact, mitigation, and approval evidence per "
    "docs/deviation_process.md."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate guideline/category/coverage artifacts")
    parser.add_argument("--seed-topics", type=Path, default=Path("data/seed_topics.yaml"))
    parser.add_argument(
        "--guideline-categories",
        type=Path,
        default=Path("data/guideline_categories.yaml"),
    )
    parser.add_argument("--todo-guidelines", type=Path, default=Path("data/todo_guidelines.yaml"))
    parser.add_argument("--coverage-matrix", type=Path, default=Path("data/coverage_matrix.csv"))
    parser.add_argument("--target-scope", type=Path, default=Path("data/target_scope.yaml"))
    parser.add_argument(
        "--fls-target-candidates",
        type=Path,
        default=Path("data/fls_target_candidates.yaml"),
    )
    parser.add_argument(
        "--decomposition-report",
        type=Path,
        default=Path("data/decomposition_report.yaml"),
    )
    parser.add_argument(
        "--no-merge-existing",
        action="store_true",
        help="Disable merge-first behavior and replace backlog from generated output",
    )
    return parser.parse_args()


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "misc"


def category_id(name: str) -> str:
    return f"CAT-{slug(name).upper()}"


def guideline_id(seed_id: str) -> str:
    suffix = seed_id.split("-", maxsplit=1)[-1]
    return f"RG-{suffix.upper()}"


def target_id(seed: dict[str, Any]) -> str:
    anchor = str(seed.get("citation_anchor_id") or "").strip()
    if anchor:
        return anchor
    chunk = str(seed.get("chunk_id") or "").strip()
    if chunk:
        return chunk
    key_material = "|".join(
        [
            str(seed.get("seed_id") or ""),
            str(seed.get("iso_ref") or ""),
            str(seed.get("reference") or ""),
        ]
    )
    digest = hashlib.sha1(key_material.encode("utf-8")).hexdigest()[:12].upper()
    return f"TARGET-{digest}"


def obligation_unit_id(seed: dict[str, Any]) -> str:
    value = str(seed.get("obligation_unit_id") or "").strip()
    if value:
        return value
    return normalize_obligation_unit(seed)


def enforcement_mode(seed: dict[str, Any]) -> str:
    candidate = str(seed.get("enforceability_hint") or "AUDIT").strip().upper()
    if candidate in {"AUTO", "AUDIT", "HYBRID"}:
        return candidate
    return "AUDIT"


def enforcement_details(mode: str) -> str:
    if mode == "AUTO":
        return (
            "Enforce with toolchain automation (rustc/clippy/rustfmt where applicable) "
            "and deterministic fixture expectations."
        )
    if mode == "HYBRID":
        return (
            "Use automated lint/static checks first, then close residual gaps with manual "
            "audit evidence."
        )
    return "Track compliance through structured manual audit checklist evidence."


def rule_statement(technical_topic: str, reference: str) -> str:
    lowered = technical_topic.lower()
    if "language subset" in lowered:
        return (
            f"Use only the approved Rust language subset for {reference} and avoid "
            "forbidden or high-risk constructs in safety-critical code."
        )
    if "defensive" in lowered:
        return (
            "Apply defensive checks and explicit error handling behaviors consistent with "
            f"{reference}."
        )
    if "complexity" in lowered:
        return f"Keep implementation complexity bounded, reviewable, and traceable to {reference}."
    if "concurrency" in lowered:
        return (
            "Constrain concurrency/shared-state patterns to auditable, deterministic approaches "
            f"for {reference}."
        )
    if "type safety" in lowered:
        return f"Use explicit, reviewed type-conversion boundaries aligned with {reference}."
    return f"Provide verifiable compliance evidence for activities mapped to {reference}."


def choose_scope(seed: dict[str, Any]) -> str:
    reference = str(seed.get("reference") or "").lower()
    topic_phrase = str(seed.get("topic_phrase") or "").lower()
    if "integration" in topic_phrase:
        return "system"
    if "module" in reference:
        return "module"
    return DEFAULT_SCOPE


def derive_decidability(seed: dict[str, Any]) -> tuple[str, str | None, str]:
    mode = enforcement_mode(seed)
    reference = str(seed.get("reference") or seed.get("iso_ref") or "mapped ISO reference")

    if mode == "AUDIT":
        return (
            "undecidable",
            None,
            (
                "The current rule shape requires reviewer judgment and contextual interpretation "
                f"for {reference}, so deterministic automated decision is not currently possible."
            ),
        )

    if mode == "AUTO":
        return (
            "decidable",
            "possible-with-clippy",
            (
                "The rule is intended to be machine-decidable and appears suitable for static lint "
                "enforcement over "
                f"{reference}, but an exact existing lint mapping is not yet locked."
            ),
        )

    return (
        "decidable",
        "possible-with-clippy",
        (
            "The rule has a decidable core plus contextual review needs; static lint coverage is "
            f"plausible for parts of {reference} and should be tracked for future Clippy support."
        ),
    )


def default_amplification(seed: dict[str, Any]) -> str:
    return (
        "Initial generic guideline derived from ISO 26262 seed "
        f"{seed.get('seed_id')} ({seed.get('iso_ref')}). This rule is expected to be decomposed "
        "into narrower Rust-specific sub-guidelines as corpus evidence grows."
    )


def default_exceptions() -> str:
    return (
        "Exception allowed only through the documented deviation process with explicit "
        "safety impact "
        "assessment, mitigation evidence, and reviewer approval."
    )


def build_examples(
    guideline_id_value: str,
    decidable: str,
    decidable_status: str | None,
) -> dict[str, dict[str, str]]:
    base = f"tests/guidelines/{guideline_id_value}/examples"
    compliant_expectation = "no_run" if decidable == "decidable" else "documented-only"

    if decidable_status == "compiler":
        non_compliant_expectation = "compile_fail"
    elif decidable_status is None:
        non_compliant_expectation = "documented-only"
    else:
        non_compliant_expectation = "compile_pass"

    return {
        "non_compliant": {
            "code_path": f"{base}/non_compliant.rs",
            "doc_path": f"{base}/non_compliant.md",
            "explanation": (
                "This example intentionally violates the guideline intent and should be used as "
                "negative evidence during rule validation."
            ),
            "compile_expectation": non_compliant_expectation,
        },
        "compliant": {
            "code_path": f"{base}/compliant.rs",
            "doc_path": f"{base}/compliant.md",
            "explanation": (
                "This example demonstrates a compliant coding pattern aligned with the guideline "
                "intent and should be used as positive evidence during rule validation."
            ),
            "compile_expectation": compliant_expectation,
        },
    }


def build_categories(seeds: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    seed_index = {str(seed.get("seed_id")): seed for seed in seeds}

    for seed in seeds:
        name = str(seed.get("category_candidate") or "Uncategorized").strip() or "Uncategorized"
        item = grouped.setdefault(
            name,
            {
                "id": category_id(name),
                "name": name,
                "default_enforcement_mode": "AUDIT",
                "seed_ids": [],
            },
        )
        item["seed_ids"].append(str(seed.get("seed_id")))

    for item in grouped.values():
        sorted_seed_ids = sorted({seed_id for seed_id in item["seed_ids"] if seed_id})
        item["seed_ids"] = sorted_seed_ids

        mode_votes = {"AUTO": 0, "AUDIT": 0, "HYBRID": 0}
        for seed_id in sorted_seed_ids:
            matching_seed = seed_index.get(seed_id)
            if matching_seed is None:
                continue
            mode_votes[enforcement_mode(matching_seed)] += 1

        winner = sorted(mode_votes.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]
        item["default_enforcement_mode"] = winner

    return {
        "version": 1,
        "categories": [
            grouped[name] for name in sorted(grouped, key=lambda value: grouped[value]["id"])
        ],
    }


def load_fls_candidate_index(path: Path) -> dict[str, dict[str, list[str]]]:
    by_target: dict[str, list[str]] = {}
    by_obligation: dict[str, list[str]] = {}

    if not path.exists():
        return {"by_target": by_target, "by_obligation": by_obligation}

    payload = read_yaml(path) or {}
    for entry in payload.get("target_candidates", []):
        target = str(entry.get("target_id") or "").strip()
        obligation = str(entry.get("obligation_unit_id") or "").strip()
        refs = []
        for candidate in entry.get("candidate_fls_refs", []):
            ref = str(candidate.get("fls_ref") or "").strip()
            if ref and ref not in refs:
                refs.append(ref)
        if target and refs:
            by_target[target] = refs
        if obligation and refs:
            by_obligation[obligation] = refs

    return {"by_target": by_target, "by_obligation": by_obligation}


def fallback_fls_refs(seed: dict[str, Any]) -> list[str]:
    text = " ".join(
        [
            str(seed.get("topic_phrase") or ""),
            str(seed.get("category_candidate") or ""),
            str(seed.get("reference") or ""),
        ]
    ).lower()
    if "concurrency" in text or "atomic" in text:
        return ["fls_concurrency_core", "fls_ownership_and_destruction_safety"]
    if "error" in text or "defensive" in text:
        return ["fls_exceptions_and_errors_core", "fls_functions_safety"]
    if "type" in text or "conversion" in text:
        return ["fls_types_and_traits_core", "fls_values_safety"]
    if "subset" in text or "table" in text:
        return ["fls_program_structure_and_compilation_core", "fls_unsafety_safety"]
    return ["fls_program_structure_and_compilation_core"]


def fls_refs_for_seed(
    seed: dict[str, Any], candidate_index: dict[str, dict[str, list[str]]]
) -> list[str]:
    target = target_id(seed)
    obligation = obligation_unit_id(seed)
    by_obligation = candidate_index.get("by_obligation") or {}
    by_target = candidate_index.get("by_target") or {}

    refs = list(by_obligation.get(obligation) or by_target.get(target) or [])
    if refs:
        return refs
    return fallback_fls_refs(seed)


def build_guideline_from_seed(seed: dict[str, Any], gid: str | None = None) -> dict[str, Any]:
    seed_id = str(seed.get("seed_id") or "").strip()
    guideline_id_value = gid or guideline_id(seed_id)
    mode = enforcement_mode(seed)
    reference = str(seed.get("reference") or seed.get("iso_ref") or "mapped ISO clause")
    technical_topic = (
        str(seed.get("category_candidate") or "Uncategorized").strip() or "Uncategorized"
    )
    decidable, decidable_status, decidability_rationale = derive_decidability(seed)
    scope = choose_scope(seed)
    examples = build_examples(guideline_id_value, decidable, decidable_status)

    evidence_paths = [
        f"tests/guidelines/{guideline_id_value}/metadata.yaml",
        examples["compliant"]["doc_path"],
        examples["non_compliant"]["doc_path"],
        f"audit_checklists/{guideline_id_value}.md",
    ]

    guideline = {
        "id": guideline_id_value,
        "category": DEFAULT_GUIDELINE_CATEGORY,
        "technical_topic": technical_topic,
        "rule_statement": rule_statement(technical_topic, reference),
        "amplification": default_amplification(seed),
        "exceptions": default_exceptions(),
        "rationale": (
            "Derived from ISO 26262 seed "
            f"{seed_id} ({seed.get('iso_ref', 'unknown reference')}) to maintain "
            "traceable coding-guideline coverage."
        ),
        "iso_seeds": [seed_id],
        "scope": scope,
        "decidable": decidable,
        "decidability_rationale": decidability_rationale,
        "state": "DRAFT",
        "enforcement_mode": mode,
        "enforcement_details": enforcement_details(mode),
        "evidence_artifacts": evidence_paths,
        "deviation_requirements": DEFAULT_DEVIATION_REQUIREMENTS,
        "examples": examples,
        "obligation_units": [obligation_unit_id(seed)],
        "rule_family_id": rule_family_id_for_target(target_id(seed)),
    }

    if decidable_status is not None:
        guideline["decidable_status"] = decidable_status

    if decidable_status == "possible-with-clippy":
        guideline["clippy_candidate_tracker"] = (
            "https://github.com/rust-lang/rust-clippy/issues/new"
            f"?title={guideline_id_value}%20candidate%20lint"
        )

    return guideline


def build_scaffold_guidelines(
    seeds: list[dict[str, Any]],
    decomposition_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    seed_index = {str(seed.get("seed_id") or "").strip(): seed for seed in seeds}
    scaffolds: list[dict[str, Any]] = []
    for request in decomposition_payload.get("scaffold_requests", []):
        seed_id = str(request.get("seed_id") or "").strip()
        target = str(request.get("target_id") or "").strip()
        ordinal = int(request.get("ordinal") or 0)
        if not seed_id or not target or ordinal < 1:
            continue
        seed = seed_index.get(seed_id)
        if seed is None:
            continue

        gid = guideline_id_for_scaffold(seed_id, target, ordinal)
        scaffold = build_guideline_from_seed(seed, gid=gid)
        parent = guideline_id(seed_id)
        scaffold["decomposition_parent"] = parent
        scaffold["rule_family_id"] = rule_family_id_for_target(target)
        scaffold["rule_statement"] = (
            scaffold["rule_statement"] + f" (decomposed sub-guideline {ordinal})"
        )
        scaffold["amplification"] = (
            f"Decomposed scaffold derived from {parent} for target {target}. "
            "Refine this draft into a narrow, Rust-specific rule with concrete examples."
        )
        scaffold["obligation_units"] = [
            str(request.get("obligation_unit_id") or obligation_unit_id(seed)).strip()
        ]
        scaffolds.append(scaffold)
    return scaffolds


def union_list(existing: Any, generated: Any) -> list[str]:
    output = []
    for item in list(existing or []) + list(generated or []):
        text = str(item).strip()
        if text and text not in output:
            output.append(text)
    return output


def merge_guidelines(
    generated_guidelines: list[dict[str, Any]],
    existing_guidelines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing_index = {
        str(item.get("id") or "").strip(): item
        for item in existing_guidelines
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }

    merged: list[dict[str, Any]] = []
    generated_ids: set[str] = set()
    for generated in generated_guidelines:
        gid = str(generated.get("id") or "").strip()
        if not gid:
            continue
        generated_ids.add(gid)

        existing = existing_index.get(gid)
        if existing is None:
            merged.append(generated)
            continue

        candidate = dict(existing)
        candidate["iso_seeds"] = union_list(existing.get("iso_seeds"), generated.get("iso_seeds"))
        candidate["obligation_units"] = union_list(
            existing.get("obligation_units"), generated.get("obligation_units")
        )
        candidate["fls_refs"] = union_list(existing.get("fls_refs"), generated.get("fls_refs"))
        candidate["evidence_artifacts"] = union_list(
            existing.get("evidence_artifacts"), generated.get("evidence_artifacts")
        )

        for key in [
            "rule_family_id",
            "decomposition_parent",
            "technical_topic",
            "scope",
            "decidable",
            "decidable_status",
            "decidability_rationale",
            "enforcement_mode",
            "enforcement_details",
            "examples",
            "deviation_requirements",
        ]:
            if key not in candidate or candidate.get(key) in (None, "", [], {}):
                if key in generated:
                    candidate[key] = generated[key]

        merged.append(candidate)

    for existing in existing_guidelines:
        gid = str(existing.get("id") or "").strip()
        if not gid or gid in generated_ids:
            continue
        merged.append(existing)

    merged.sort(key=lambda item: str(item.get("id") or ""))
    return merged


def build_guidelines_payload(
    seeds: list[dict[str, Any]],
    candidate_index: dict[str, dict[str, list[str]]],
    decomposition_payload: dict[str, Any],
    existing_payload: dict[str, Any] | None,
    merge_existing: bool,
) -> dict[str, Any]:
    generated = []
    sorted_seeds = sorted(seeds, key=lambda seed: str(seed.get("seed_id") or ""))
    for seed in sorted_seeds:
        seed_id = str(seed.get("seed_id") or "").strip()
        if not seed_id:
            continue
        guideline = build_guideline_from_seed(seed)
        guideline["fls_refs"] = fls_refs_for_seed(seed, candidate_index)
        generated.append(guideline)

    scaffolds = build_scaffold_guidelines(seeds, decomposition_payload)
    for scaffold in scaffolds:
        parent_seed = scaffold.get("iso_seeds", [None])[0]
        if parent_seed:
            parent_seed_payload = next(
                (
                    seed
                    for seed in seeds
                    if str(seed.get("seed_id") or "").strip() == str(parent_seed).strip()
                ),
                None,
            )
            if parent_seed_payload is not None:
                scaffold["fls_refs"] = fls_refs_for_seed(parent_seed_payload, candidate_index)
        generated.append(scaffold)

    if not merge_existing or not existing_payload:
        generated.sort(key=lambda item: str(item.get("id") or ""))
        return {"version": 1, "guidelines": generated}

    merged = merge_guidelines(generated, existing_payload.get("guidelines") or [])
    return {"version": 1, "guidelines": merged}


def build_coverage(
    seeds: list[dict[str, Any]],
    guidelines_payload: dict[str, Any],
) -> list[dict[str, str]]:
    seed_index = {str(seed.get("seed_id")): seed for seed in seeds}
    rows: list[dict[str, str]] = []

    for guideline in guidelines_payload.get("guidelines", []):
        gid = str(guideline.get("id") or "").strip()
        evidence = ""
        evidence_artifacts = guideline.get("evidence_artifacts") or []
        if evidence_artifacts:
            evidence = str(evidence_artifacts[0]).strip()

        fls_refs = [
            str(ref).strip() for ref in guideline.get("fls_refs", []) if str(ref).strip()
        ] or [""]
        obligation_units = [
            str(unit).strip() for unit in guideline.get("obligation_units", []) if str(unit).strip()
        ]

        for seed_id in guideline.get("iso_seeds", []):
            sid = str(seed_id).strip()
            seed = seed_index.get(sid)
            if seed is None:
                continue

            target = target_id(seed)
            obligation = obligation_unit_id(seed)
            if obligation_units and obligation not in obligation_units:
                obligation = obligation_units[0]

            for fls_ref in fls_refs:
                rows.append(
                    {
                        "target_id": target,
                        "obligation_unit_id": obligation,
                        "seed_id": sid,
                        "guideline_id": gid,
                        "fls_ref": fls_ref,
                        "evidence_path": evidence,
                    }
                )

    return sorted(
        rows,
        key=lambda row: (
            row["target_id"],
            row["obligation_unit_id"],
            row["seed_id"],
            row["guideline_id"],
            row["fls_ref"],
        ),
    )


def write_coverage_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "target_id",
                "obligation_unit_id",
                "seed_id",
                "guideline_id",
                "fls_ref",
                "evidence_path",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    root = repo_root()
    seed_topics_path = root / args.seed_topics

    if not seed_topics_path.exists():
        print(f"[guideline-gen][error] missing seed topics: {seed_topics_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL

    payload = read_yaml(seed_topics_path) or {}
    seed_topics = payload.get("seed_topics") or []
    if not seed_topics:
        print("[guideline-gen][error] seed_topics is empty")
        return EXIT_RUNTIME_FAIL

    candidate_index = load_fls_candidate_index(root / args.fls_target_candidates)
    decomposition_payload = {}
    decomposition_path = root / args.decomposition_report
    if decomposition_path.exists():
        decomposition_payload = read_yaml(decomposition_path) or {}

    existing_payload: dict[str, Any] | None = None
    guidelines_path = root / args.todo_guidelines
    if guidelines_path.exists():
        existing_payload = load_guidelines_payload(guidelines_path)

    categories_payload = build_categories(seed_topics)
    guidelines_payload = build_guidelines_payload(
        seed_topics,
        candidate_index,
        decomposition_payload,
        existing_payload,
        merge_existing=not args.no_merge_existing,
    )
    coverage_rows = build_coverage(seed_topics, guidelines_payload)
    target_scope_payload = {
        "version": 1,
        "in_scope_target_ids": sorted(
            {row["target_id"] for row in coverage_rows if row["target_id"]}
        ),
    }

    categories_path = root / args.guideline_categories
    coverage_path = root / args.coverage_matrix
    target_scope_path = root / args.target_scope

    write_yaml(categories_path, categories_payload)
    write_yaml(guidelines_path, guidelines_payload)
    write_coverage_csv(coverage_path, coverage_rows)
    write_yaml(target_scope_path, target_scope_payload)

    print(
        "[guideline-gen] wrote "
        f"categories={len(categories_payload['categories'])} "
        f"guidelines={len(guidelines_payload['guidelines'])} "
        f"coverage_rows={len(coverage_rows)}"
    )
    print(f"[guideline-gen] categories -> {categories_path.relative_to(root)}")
    print(f"[guideline-gen] guidelines -> {guidelines_path.relative_to(root)}")
    print(f"[guideline-gen] coverage -> {coverage_path.relative_to(root)}")
    print(f"[guideline-gen] target_scope -> {target_scope_path.relative_to(root)}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
