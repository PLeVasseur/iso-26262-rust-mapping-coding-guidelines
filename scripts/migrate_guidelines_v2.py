#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from _common import EXIT_SUCCESS, normalize_guideline_record, read_yaml, repo_root, write_yaml

VALID_CATEGORY = {"Mandatory", "Required", "Advisory", "Disapplied"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate guideline backlog records to v2 shape")
    parser.add_argument("--input", type=Path, default=Path("data/todo_guidelines.yaml"))
    parser.add_argument("--output", type=Path, default=Path("data/todo_guidelines.yaml"))
    return parser.parse_args()


def default_examples(guideline_id: str, decidable: str) -> dict[str, Any]:
    base = f"tests/guidelines/{guideline_id}/examples"
    compliant_expectation = "no_run" if decidable == "decidable" else "documented-only"
    non_compliant_expectation = "compile_pass" if decidable == "decidable" else "documented-only"
    return {
        "non_compliant": {
            "code_path": f"{base}/non_compliant.rs",
            "doc_path": f"{base}/non_compliant.md",
            "explanation": "Migrated non-compliant example explanation; refine per rule.",
            "compile_expectation": non_compliant_expectation,
        },
        "compliant": {
            "code_path": f"{base}/compliant.rs",
            "doc_path": f"{base}/compliant.md",
            "explanation": "Migrated compliant example explanation; refine per rule.",
            "compile_expectation": compliant_expectation,
        },
    }


def migrate_guideline(record: dict[str, Any]) -> dict[str, Any]:
    record = normalize_guideline_record(record)
    guideline_id = str(record.get("id") or "")
    old_category = str(record.get("category") or "").strip()
    technical_topic = str(record.get("technical_topic") or "").strip()

    if not technical_topic:
        if old_category and old_category not in VALID_CATEGORY:
            technical_topic = old_category
        else:
            technical_topic = "Uncategorized"

    category = old_category if old_category in VALID_CATEGORY else "Required"
    scope = str(record.get("scope") or "crate")
    if scope not in {"system", "crate", "module"}:
        scope = "crate"

    decidable = str(record.get("decidable") or "undecidable")
    if decidable not in {"decidable", "undecidable"}:
        decidable = "undecidable"

    migrated = {
        "id": guideline_id,
        "category": category,
        "technical_topic": technical_topic,
        "rule_statement": str(record.get("rule_statement") or "Guideline statement pending."),
        "amplification": str(
            record.get("amplification")
            or "Migrated from legacy guideline shape; amplification requires refinement."
        ),
        "exceptions": str(
            record.get("exceptions")
            or "Exception allowed only through documented deviation process approval."
        ),
        "rationale": str(record.get("rationale") or "Migrated rationale pending refinement."),
        "iso_seeds": record.get("iso_seeds") or [],
        "scope": scope,
        "decidable": decidable,
        "decidability_rationale": str(
            record.get("decidability_rationale")
            or "Migrated legacy record; decidability rationale requires refinement."
        ),
        "state": str(record.get("state") or "DRAFT"),
        "enforcement_mode": str(record.get("enforcement_mode") or "AUDIT"),
        "enforcement_details": str(
            record.get("enforcement_details") or "Migrated enforcement details pending refinement."
        ),
        "evidence_artifacts": record.get("evidence_artifacts") or [],
        "deviation_requirements": str(
            record.get("deviation_requirements")
            or "Document deviation rationale, impact, mitigation, and approval evidence."
        ),
        "examples": record.get("examples") or default_examples(guideline_id, decidable),
    }

    if decidable == "decidable":
        status = record.get("decidable_status")
        if status:
            migrated["decidable_status"] = status
            if status == "clippy":
                if record.get("clippy_lint_id"):
                    migrated["clippy_lint_id"] = record.get("clippy_lint_id")
                if record.get("clippy_lint_url"):
                    migrated["clippy_lint_url"] = record.get("clippy_lint_url")
            if status == "possible-with-clippy" and record.get("clippy_candidate_tracker"):
                migrated["clippy_candidate_tracker"] = record.get("clippy_candidate_tracker")

    return migrated


def main() -> int:
    args = parse_args()
    root = repo_root()
    input_path = root / args.input
    output_path = root / args.output

    payload = read_yaml(input_path) or {}
    guidelines = payload.get("guidelines") or []
    migrated = [migrate_guideline(item) for item in guidelines if isinstance(item, dict)]

    write_yaml(
        output_path,
        {
            "version": int(payload.get("version") or 1),
            "guidelines": migrated,
        },
    )
    print(f"[guideline-migrate] migrated guidelines={len(migrated)}")
    print(f"[guideline-migrate] output -> {output_path.relative_to(root)}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
