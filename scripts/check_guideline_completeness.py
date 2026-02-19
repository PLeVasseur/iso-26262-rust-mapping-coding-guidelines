#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from _common import (
    EXIT_POLICY_FAIL,
    EXIT_SUCCESS,
    load_guidelines_payload,
    read_yaml,
    repo_root,
    write_json,
)

VALID_CATEGORY = {"Mandatory", "Required", "Advisory", "Disapplied"}
VALID_SCOPE = {"system", "crate", "module"}
VALID_DECIDABLE = {"decidable", "undecidable"}
VALID_DECIDABLE_STATUS = {
    "compiler",
    "clippy",
    "possible-with-clippy",
    "impossible-with-clippy",
}
VALID_COMPLIANT_EXPECTATION = {"compile_pass", "no_run", "documented-only"}
VALID_NON_COMPLIANT_EXPECTATION = {"compile_fail", "compile_pass", "documented-only"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check guideline records for v3 completeness")
    parser.add_argument("--todo-guidelines", type=Path, default=Path("data/todo_guidelines.yaml"))
    parser.add_argument(
        "--clippy-catalog", type=Path, default=Path("data/clippy_lints_catalog.yaml")
    )
    parser.add_argument("--fls-inventory", type=Path, default=Path("data/fls_inventory.yaml"))
    parser.add_argument("--require-fls-refs", action="store_true")
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def is_valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def has_rust_fence(markdown_text: str) -> bool:
    pattern = re.compile(r"```(?:rust|compile_fail|no_run|ignore|should_panic|edition20\d\d)?")
    return bool(pattern.search(markdown_text))


def is_placeholder_text(value: str) -> bool:
    lowered = value.strip().lower()
    return not lowered or "placeholder" in lowered or "pending" in lowered


def main() -> int:
    args = parse_args()
    root = repo_root()
    guidelines_path = root / args.todo_guidelines
    catalog_path = root / args.clippy_catalog
    fls_inventory_path = root / args.fls_inventory

    payload = load_guidelines_payload(guidelines_path)
    guidelines = payload.get("guidelines") or []
    catalog_payload = read_yaml(catalog_path) or {}
    lint_url_map = {
        str(item.get("id") or ""): str(item.get("url") or "")
        for item in catalog_payload.get("lints", [])
        if isinstance(item, dict)
    }

    valid_fls_refs: set[str] = set()
    if fls_inventory_path.exists():
        fls_payload = read_yaml(fls_inventory_path) or {}
        valid_fls_refs = {
            str(item.get("fls_ref") or "").strip()
            for item in fls_payload.get("paragraphs", [])
            if str(item.get("fls_ref") or "").strip()
        }

    errors: list[str] = []
    warnings: list[str] = []

    for guideline in guidelines:
        if not isinstance(guideline, dict):
            errors.append("guideline entry is not an object")
            continue

        guideline_id = str(guideline.get("id") or "").strip()
        if not guideline_id:
            errors.append("guideline missing id")
            continue

        prefix = f"{guideline_id}:"

        category = str(guideline.get("category") or "")
        if category not in VALID_CATEGORY:
            errors.append(f"{prefix} invalid category `{category}`")

        technical_topic = str(guideline.get("technical_topic") or "").strip()
        if not technical_topic:
            errors.append(f"{prefix} technical_topic missing")

        scope = str(guideline.get("scope") or "")
        if scope not in VALID_SCOPE:
            errors.append(f"{prefix} invalid scope `{scope}`")

        fls_refs = [str(ref).strip() for ref in guideline.get("fls_refs", []) if str(ref).strip()]
        guideline_state = str(guideline.get("state") or "")
        if not fls_refs:
            message = f"{prefix} missing fls_refs"
            if args.require_fls_refs and guideline_state != "DEPRECATED":
                errors.append(message)
            else:
                warnings.append(message)
        elif valid_fls_refs:
            for ref in fls_refs:
                if ref not in valid_fls_refs:
                    errors.append(f"{prefix} fls_ref not in inventory: {ref}")
        elif fls_inventory_path.exists():
            warnings.append(f"{prefix} FLS inventory has no paragraphs to validate refs")
        else:
            warnings.append(
                f"{prefix} FLS inventory missing; cannot validate fls_refs against catalog"
            )

        obligation_units = [
            str(unit).strip() for unit in guideline.get("obligation_units", []) if str(unit).strip()
        ]
        if not obligation_units:
            warnings.append(f"{prefix} obligation_units not set")

        decidable = str(guideline.get("decidable") or "")
        if decidable not in VALID_DECIDABLE:
            errors.append(f"{prefix} invalid decidable `{decidable}`")

        status = guideline.get("decidable_status")
        if decidable == "undecidable":
            if status is not None:
                errors.append(f"{prefix} undecidable guideline must not define decidable_status")
            for forbidden in ["clippy_lint_id", "clippy_lint_url", "clippy_candidate_tracker"]:
                if forbidden in guideline:
                    errors.append(f"{prefix} undecidable guideline must not define `{forbidden}`")
        else:
            if status is None:
                errors.append(f"{prefix} decidable guideline missing decidable_status")
            else:
                status_text = str(status)
                if status_text not in VALID_DECIDABLE_STATUS:
                    errors.append(f"{prefix} invalid decidable_status `{status_text}`")

                if status_text == "clippy":
                    lint_id = str(guideline.get("clippy_lint_id") or "").strip()
                    lint_url = str(guideline.get("clippy_lint_url") or "").strip()
                    if not lint_id:
                        errors.append(f"{prefix} clippy status requires clippy_lint_id")
                    if not lint_url:
                        errors.append(f"{prefix} clippy status requires clippy_lint_url")
                    if lint_id and lint_id not in lint_url_map:
                        errors.append(f"{prefix} clippy_lint_id not found in catalog: {lint_id}")
                    if (
                        lint_id
                        and lint_url_map.get(lint_id)
                        and lint_url != lint_url_map.get(lint_id)
                    ):
                        errors.append(
                            f"{prefix} clippy_lint_url does not match catalog for {lint_id}"
                        )

                if status_text == "possible-with-clippy":
                    tracker = str(guideline.get("clippy_candidate_tracker") or "").strip()
                    if not tracker:
                        errors.append(
                            f"{prefix} possible-with-clippy requires clippy_candidate_tracker"
                        )
                    elif not is_valid_url(tracker):
                        errors.append(f"{prefix} invalid clippy_candidate_tracker url")

                if status_text == "compiler":
                    non_compliant = (guideline.get("examples") or {}).get("non_compliant") or {}
                    expectation = str(non_compliant.get("compile_expectation") or "")
                    if expectation != "compile_fail":
                        reason = str(
                            non_compliant.get("expectation_exception_reason") or ""
                        ).strip()
                        if not reason:
                            errors.append(
                                f"{prefix} compiler rule requires compile_fail or exception reason"
                            )

        for field_name in [
            "rule_statement",
            "amplification",
            "exceptions",
            "rationale",
            "decidability_rationale",
            "deviation_requirements",
        ]:
            value = str(guideline.get(field_name) or "")
            if is_placeholder_text(value):
                errors.append(f"{prefix} `{field_name}` missing or placeholder text")

        examples = guideline.get("examples") or {}
        for side, valid_expectations in [
            ("compliant", VALID_COMPLIANT_EXPECTATION),
            ("non_compliant", VALID_NON_COMPLIANT_EXPECTATION),
        ]:
            entry = examples.get(side) or {}
            code_path = str(entry.get("code_path") or "").strip()
            doc_path = str(entry.get("doc_path") or "").strip()
            explanation = str(entry.get("explanation") or "").strip()
            expectation = str(entry.get("compile_expectation") or "").strip()

            if not code_path:
                errors.append(f"{prefix} examples.{side}.code_path missing")
            if not doc_path:
                errors.append(f"{prefix} examples.{side}.doc_path missing")
            if is_placeholder_text(explanation):
                errors.append(f"{prefix} examples.{side}.explanation missing or placeholder")
            if expectation not in valid_expectations:
                errors.append(
                    f"{prefix} examples.{side}.compile_expectation invalid `{expectation}`"
                )

            if code_path:
                absolute_code = root / code_path
                if not absolute_code.exists():
                    errors.append(f"{prefix} missing example code file: {code_path}")
            if doc_path:
                absolute_doc = root / doc_path
                if not absolute_doc.exists():
                    errors.append(f"{prefix} missing example doc file: {doc_path}")
                else:
                    markdown = absolute_doc.read_text(encoding="utf-8")
                    if not has_rust_fence(markdown):
                        errors.append(f"{prefix} example doc missing rust code fence: {doc_path}")

        if (
            str(guideline.get("state") or "") == "ENFORCED"
            and str(guideline.get("category") or "") == "Disapplied"
        ):
            warnings.append(f"{prefix} ENFORCED + Disapplied combination should be reviewed")

    report = {
        "guideline_count": len(guidelines),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "ok": len(errors) == 0,
    }

    if args.json_output:
        write_json(args.json_output, report)

    if report["ok"]:
        print(
            "[guideline-completeness] "
            f"ok guidelines={report['guideline_count']} warnings={report['warning_count']}"
        )
        for warning in warnings:
            print(f"[guideline-completeness][warn] {warning}")
        return EXIT_SUCCESS

    print(f"[guideline-completeness] failed with {len(errors)} error(s)")
    for error in errors:
        print(f"[guideline-completeness][error] {error}")
    return EXIT_POLICY_FAIL


if __name__ == "__main__":
    sys.exit(main())
