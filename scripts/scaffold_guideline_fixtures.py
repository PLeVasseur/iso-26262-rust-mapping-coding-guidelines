#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from _common import EXIT_SUCCESS, load_guidelines_payload, repo_root, write_yaml

AUTO_FILES = {
    "auto/compliant.rs": "// compliant fixture placeholder\n",
    "auto/violating.rs": "// violating fixture placeholder\n",
    "auto/expected.txt": "expected tool output placeholder\n",
}

AUDIT_FILES = {
    "audit/reviewer_steps.md": "# Reviewer Steps\n\n1. Inspect relevant changes.\n",
    "audit/expected_findings.md": "# Expected Findings\n\n- Placeholder finding notes.\n",
}

HYBRID_FILES = {
    "hybrid/residual_audit_case.md": "# Residual Audit Case\n\nDocument non-automated checks.\n",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create guideline fixture folders from backlog")
    parser.add_argument("--todo-guidelines", type=Path, default=Path("data/todo_guidelines.yaml"))
    parser.add_argument("--tests-root", type=Path, default=Path("tests/guidelines"))
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing metadata and placeholder fixture files",
    )
    return parser.parse_args()


def write_text_file(path: Path, content: str, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def fallback_expected_outcome(kind: str, expectation: str) -> str:
    normalized = expectation.strip()
    if normalized == "compile_fail":
        return "compile_fail"
    if normalized == "documented-only":
        return "documented_only"
    if kind == "non_compliant":
        return "runtime_panic"
    return "assertion_pass"


def code_fence_tag(expectation: str, expected_outcome: str) -> str:
    outcome = expected_outcome.strip().lower()
    if outcome == "compile_fail":
        return "compile_fail"
    if outcome == "runtime_panic":
        return "should_panic"
    if outcome == "documented_only":
        return "no_run"
    if expectation == "compile_fail":
        return "compile_fail"
    if expectation == "no_run":
        return "no_run"
    return "rust"


def extract_code_body(markdown_content: str) -> str:
    match = re.search(r"```[^\n]*\n(?P<code>[\s\S]*?)\n```", markdown_content)
    if not match:
        return "fn main() {}\n"
    return f"{match.group('code').rstrip()}\n"


def build_example_markdown(
    rule_id: str,
    kind: str,
    expectation: str,
    expected_outcome: str,
    explanation: str,
    verification_notes: str,
) -> str:
    fence_tag = code_fence_tag(expectation, expected_outcome)
    outcome = expected_outcome.strip().lower()
    if outcome == "compile_fail":
        code = (
            "fn main() {\n"
            "    // Intentional compile failure for non-compliant compiler-checkable example\n"
            '    let value: u32 = "not-a-number";\n'
            "    let _ = value;\n"
            "}\n"
        )
    elif outcome == "runtime_panic":
        code = (
            "fn main() {\n"
            "    let values = [1_u32, 2_u32, 3_u32];\n"
            "    let idx = values.len();\n"
            "    let _ = values[idx];\n"
            "}\n"
        )
    elif kind == "non_compliant":
        code = (
            "fn main() {\n"
            "    // Non-compliant placeholder; replace with rule-specific violation.\n"
            "    let input = [1_u32, 2_u32, 3_u32];\n"
            "    let mut total = 0_u32;\n"
            "    for item in input {\n"
            "        total += item;\n"
            "    }\n"
            "    // Missing safety check on purpose for negative evidence.\n"
            "    if total == 6 {\n"
            "        // Intentionally weak behavior for lint-style non-compliance examples.\n"
            '        println!("{}", total);\n'
            "    }\n"
            "}\n"
        )
    else:
        code = (
            "fn main() {\n"
            "    let values = [1_u32, 2_u32, 3_u32];\n"
            "    let total: u32 = values.into_iter().sum();\n"
            "    assert_eq!(total, 6);\n"
            "}\n"
        )

    lines = [
        f"# {kind.replace('_', ' ').title()} Example: {rule_id}",
        "",
        explanation,
        "",
        f"Expected outcome: `{outcome or 'documented_only'}`.",
    ]
    if verification_notes.strip():
        lines.extend(["", f"Verification notes: {verification_notes.strip()}"])
    lines.extend(
        [
            "",
            f"```{fence_tag}",
            code.rstrip(),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def build_metadata_payload(guideline: dict, rule_id: str, mode: str) -> dict:
    payload = {
        "version": 2,
        "rule_id": rule_id,
        "category": guideline.get("category"),
        "technical_topic": guideline.get("technical_topic"),
        "mode": mode,
        "scope": guideline.get("scope"),
        "decidable": guideline.get("decidable"),
        "state": guideline.get("state", "DRAFT"),
        "iso_seeds": guideline.get("iso_seeds") or [],
        "pass_criteria": "Compliant example meets expected compile/lint behavior.",
        "fail_criteria": "Non-compliant example triggers expected compile/lint behavior.",
    }
    if guideline.get("decidable_status") is not None:
        payload["decidable_status"] = guideline.get("decidable_status")
    if guideline.get("clippy_lint_id"):
        payload["clippy_lint_id"] = guideline.get("clippy_lint_id")
    if guideline.get("clippy_lint_url"):
        payload["clippy_lint_url"] = guideline.get("clippy_lint_url")
    if guideline.get("clippy_candidate_tracker"):
        payload["clippy_candidate_tracker"] = guideline.get("clippy_candidate_tracker")
    return payload


def scaffold_examples(rule_dir: Path, examples: dict, overwrite: bool) -> None:
    for kind in ["compliant", "non_compliant"]:
        example = examples.get(kind) or {}
        doc_rel = str(example.get("doc_path") or "")
        code_rel = str(example.get("code_path") or "")
        explanation = str(example.get("explanation") or "Example explanation pending.")
        expectation = str(example.get("compile_expectation") or "documented-only")
        expected_outcome = str(example.get("expected_outcome") or "").strip()
        if not expected_outcome:
            expected_outcome = fallback_expected_outcome(kind, expectation)
        verification_notes = str(example.get("verification_notes") or "")

        if not doc_rel or not code_rel:
            continue

        doc_path = rule_dir.parents[2] / doc_rel
        code_path = rule_dir.parents[2] / code_rel
        rule_id = rule_dir.name

        markdown = build_example_markdown(
            rule_id,
            kind,
            expectation,
            expected_outcome,
            explanation,
            verification_notes,
        )
        write_text_file(doc_path, markdown, overwrite)
        write_text_file(code_path, extract_code_body(markdown), overwrite)


def main() -> int:
    args = parse_args()
    root = repo_root()
    backlog_path = root / args.todo_guidelines
    tests_root = root / args.tests_root

    payload = load_guidelines_payload(backlog_path)
    guidelines = payload.get("guidelines") or []

    processed = 0
    for guideline in guidelines:
        rule_id = str(guideline.get("id") or "").strip()
        if not rule_id:
            continue

        mode = str(guideline.get("enforcement_mode") or "AUDIT").strip().upper()
        rule_dir = tests_root / rule_id
        rule_dir.mkdir(parents=True, exist_ok=True)

        metadata_path = rule_dir / "metadata.yaml"
        metadata_payload = build_metadata_payload(guideline, rule_id, mode)
        if not metadata_path.exists() or args.overwrite:
            write_yaml(metadata_path, metadata_payload)

        scaffold_examples(rule_dir, guideline.get("examples") or {}, args.overwrite)

        for rel_path, content in AUTO_FILES.items():
            if mode in {"AUTO", "HYBRID"}:
                write_text_file(rule_dir / rel_path, content, args.overwrite)

        for rel_path, content in AUDIT_FILES.items():
            if mode in {"AUDIT", "HYBRID"}:
                write_text_file(rule_dir / rel_path, content, args.overwrite)

        for rel_path, content in HYBRID_FILES.items():
            if mode == "HYBRID":
                write_text_file(rule_dir / rel_path, content, args.overwrite)

        processed += 1

    print(f"[fixture-scaffold] processed rules={processed} -> {tests_root.relative_to(root)}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
