#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

from _common import EXIT_RUNTIME_FAIL, EXIT_SUCCESS, read_yaml, repo_root, write_yaml

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


def build_guidelines(seeds: list[dict[str, Any]]) -> dict[str, Any]:
    guidelines: list[dict[str, Any]] = []
    sorted_seeds = sorted(seeds, key=lambda seed: str(seed.get("seed_id") or ""))

    for seed in sorted_seeds:
        seed_id = str(seed.get("seed_id") or "").strip()
        if not seed_id:
            continue

        gid = guideline_id(seed_id)
        mode = enforcement_mode(seed)
        reference = str(seed.get("reference") or seed.get("iso_ref") or "mapped ISO clause")
        technical_topic = (
            str(seed.get("category_candidate") or "Uncategorized").strip() or "Uncategorized"
        )
        decidable, decidable_status, decidability_rationale = derive_decidability(seed)
        scope = choose_scope(seed)
        examples = build_examples(gid, decidable, decidable_status)

        evidence_paths = [
            f"tests/guidelines/{gid}/metadata.yaml",
            examples["compliant"]["doc_path"],
            examples["non_compliant"]["doc_path"],
            f"audit_checklists/{gid}.md",
        ]

        guideline = {
            "id": gid,
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
        }

        if decidable_status is not None:
            guideline["decidable_status"] = decidable_status

        if decidable_status == "possible-with-clippy":
            guideline["clippy_candidate_tracker"] = (
                "https://github.com/rust-lang/rust-clippy/issues/new"
                f"?title={gid}%20candidate%20lint"
            )

        guidelines.append(guideline)

    return {"version": 1, "guidelines": guidelines}


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

        for seed_id in guideline.get("iso_seeds", []):
            sid = str(seed_id).strip()
            seed = seed_index.get(sid)
            if seed is None:
                continue

            rows.append(
                {
                    "target_id": target_id(seed),
                    "seed_id": sid,
                    "guideline_id": gid,
                    "evidence_path": evidence,
                }
            )

    return sorted(rows, key=lambda row: (row["target_id"], row["seed_id"], row["guideline_id"]))


def write_coverage_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["target_id", "seed_id", "guideline_id", "evidence_path"],
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

    categories_payload = build_categories(seed_topics)
    guidelines_payload = build_guidelines(seed_topics)
    coverage_rows = build_coverage(seed_topics, guidelines_payload)
    target_scope_payload = {
        "version": 1,
        "in_scope_target_ids": sorted(
            {row["target_id"] for row in coverage_rows if row["target_id"]}
        ),
    }

    categories_path = root / args.guideline_categories
    guidelines_path = root / args.todo_guidelines
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
