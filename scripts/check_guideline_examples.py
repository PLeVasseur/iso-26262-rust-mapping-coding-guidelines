#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from _common import (
    EXIT_POLICY_FAIL,
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    load_guidelines_payload,
    repo_root,
    run_command,
    write_json,
)

FENCE_PATTERN = re.compile(r"```(?P<tag>[^\n`]*)\n(?P<code>[\s\S]*?)\n```")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute compile/lint checks for guideline examples"
    )
    parser.add_argument("--todo-guidelines", type=Path, default=Path("data/todo_guidelines.yaml"))
    parser.add_argument(
        "--work-root",
        type=Path,
        default=Path(".cache/guideline_example_checks"),
        help="Temporary harness root for extracted example code",
    )
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def parse_first_rust_fence(markdown_text: str) -> tuple[str, str] | None:
    for match in FENCE_PATTERN.finditer(markdown_text):
        tag = match.group("tag").strip()
        code = match.group("code")
        primary_tag = tag.split(",", maxsplit=1)[0].strip().split(" ", maxsplit=1)[0].strip()
        if primary_tag in {"", "rust", "compile_fail", "no_run", "ignore", "should_panic"}:
            return primary_tag or "rust", code
    return None


def rustdoc_expectation_matches(expectation: str, fence_tag: str) -> bool:
    if expectation == "compile_fail":
        return fence_tag == "compile_fail"
    if expectation == "no_run":
        return fence_tag == "no_run"
    if expectation == "compile_pass":
        return fence_tag in {"rust", ""}
    if expectation == "documented-only":
        return True
    return False


def write_harness(manifest_path: Path, source_code: str) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        '[package]\nname = "guideline-example"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )

    src_main = manifest_path.parent / "src" / "main.rs"
    src_main.parent.mkdir(parents=True, exist_ok=True)
    src_main.write_text(
        "#![allow(dead_code, unused_variables, unused_imports)]\n" + source_code,
        encoding="utf-8",
    )


def run_clippy_check(
    lint_id: str,
    harness_dir: Path,
    expect_failure: bool,
) -> tuple[bool, str]:
    manifest_path = harness_dir / "Cargo.toml"
    completed = run_command(
        [
            "cargo",
            "clippy",
            "--manifest-path",
            str(manifest_path),
            "--quiet",
            "--",
            "-D",
            "warnings",
            "-D",
            f"clippy::{lint_id}",
        ],
        cwd=harness_dir,
    )

    combined_output = f"{completed.stdout}{completed.stderr}"
    if expect_failure:
        if completed.returncode == 0:
            return False, "expected clippy failure but command succeeded"
        if lint_id not in combined_output:
            return (
                False,
                f"expected clippy output to reference lint id `{lint_id}`",
            )
        return True, ""

    if completed.returncode != 0:
        return False, "expected clippy success but command failed"
    return True, ""


def main() -> int:
    args = parse_args()
    root = repo_root()
    todo_path = root / args.todo_guidelines
    payload = load_guidelines_payload(todo_path)
    guidelines = payload.get("guidelines") or []
    work_root = root / args.work_root

    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    warnings: list[str] = []
    checked = 0

    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if not guideline_id:
            continue

        decidable_status = guideline.get("decidable_status")
        examples = guideline.get("examples") or {}

        for side in ["compliant", "non_compliant"]:
            example = examples.get(side) or {}
            doc_rel = str(example.get("doc_path") or "").strip()
            expectation = str(example.get("compile_expectation") or "").strip()
            if not doc_rel:
                errors.append(f"{guideline_id}:{side} missing doc_path")
                continue

            doc_path = root / doc_rel
            if not doc_path.exists():
                errors.append(f"{guideline_id}:{side} missing doc file {doc_rel}")
                continue

            markdown = doc_path.read_text(encoding="utf-8")
            parsed_fence = parse_first_rust_fence(markdown)
            if parsed_fence is None:
                errors.append(f"{guideline_id}:{side} missing Rust fenced block in {doc_rel}")
                continue

            fence_tag, source_code = parsed_fence
            if not rustdoc_expectation_matches(expectation, fence_tag):
                errors.append(
                    f"{guideline_id}:{side} compile_expectation `{expectation}` does not match "
                    f"fence `{fence_tag}`"
                )

            if expectation != "documented-only":
                completed = run_command(["rustdoc", "--test", str(doc_path)], cwd=root)
                checked += 1
                if completed.returncode != 0:
                    errors.append(f"{guideline_id}:{side} rustdoc --test failed for {doc_rel}")

            if decidable_status == "clippy":
                lint_id = str(guideline.get("clippy_lint_id") or "").strip()
                if not lint_id:
                    errors.append(f"{guideline_id}:{side} missing clippy_lint_id for clippy rule")
                    continue

                harness_dir = work_root / guideline_id / side
                write_harness(harness_dir / "Cargo.toml", source_code)
                expect_failure = side == "non_compliant"
                ok, reason = run_clippy_check(lint_id, harness_dir, expect_failure=expect_failure)
                checked += 1
                if not ok:
                    errors.append(f"{guideline_id}:{side} clippy check failed: {reason}")
            elif decidable_status in {"possible-with-clippy", "impossible-with-clippy"}:
                warnings.append(
                    f"{guideline_id}:{side} clippy not executed for status `{decidable_status}`"
                )

    report: dict[str, Any] = {
        "checked_examples": checked,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "ok": len(errors) == 0,
    }

    if args.json_output:
        write_json(args.json_output, report)

    if errors:
        print(f"[guideline-examples] failed with {len(errors)} error(s)")
        for error in errors:
            print(f"[guideline-examples][error] {error}")
        return EXIT_POLICY_FAIL

    print(f"[guideline-examples] ok checked={checked} warnings={len(warnings)}")
    for warning in warnings:
        print(f"[guideline-examples][warn] {warning}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FileNotFoundError as exc:
        print(f"[guideline-examples][error] runtime dependency missing: {exc}")
        sys.exit(EXIT_RUNTIME_FAIL)
