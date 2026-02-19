#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from _common import (
    EXIT_POLICY_FAIL,
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    load_guidelines_payload,
    read_yaml,
    repo_root,
    run_command,
    write_json,
)

FENCE_PATTERN = re.compile(r"```(?P<tag>[^\n`]*)\n(?P<code>[\s\S]*?)\n```")
ASSERTION_PATTERN = re.compile(
    r"\b(?:assert!?|assert_eq!|assert_ne!|debug_assert!|debug_assert_eq!|debug_assert_ne!)"
)
VALID_OUTCOMES = {
    "assertion_pass",
    "compile_fail",
    "runtime_panic",
    "lint_trigger",
    "documented_only",
}
NEGATIVE_OUTCOMES = {"compile_fail", "runtime_panic", "lint_trigger"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute outcome-aware compile/lint checks for guideline examples"
    )
    parser.add_argument("--todo-guidelines", type=Path, default=Path("data/todo_guidelines.yaml"))
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("config/example_quality_policy.yaml"),
        help="Example quality policy file",
    )
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
        if primary_tag in {
            "",
            "rust",
            "compile_fail",
            "no_run",
            "ignore",
            "should_panic",
        }:
            return primary_tag or "rust", code
    return None


def rustdoc_expectation_matches(expectation: str, fence_tag: str) -> bool:
    if expectation == "compile_fail":
        return fence_tag == "compile_fail"
    if expectation == "no_run":
        return fence_tag == "no_run"
    if expectation == "compile_pass":
        return fence_tag in {"rust", "", "should_panic"}
    if expectation == "documented-only":
        return True
    return False


def normalize_outcome(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized == "documented-only":
        return "documented_only"
    return normalized


def infer_expected_outcome(
    side: str,
    compile_expectation: str,
    decidable_status: str,
    decidable: str,
) -> str:
    compile_expectation = compile_expectation.strip()
    if compile_expectation == "compile_fail":
        return "compile_fail"
    if compile_expectation == "documented-only":
        return "documented_only"
    if side == "compliant":
        return "assertion_pass" if decidable == "decidable" else "documented_only"
    if decidable_status == "clippy":
        return "lint_trigger"
    if decidable_status == "possible-with-clippy":
        return "runtime_panic"
    if decidable_status == "compiler":
        return "compile_fail"
    return "runtime_panic"


def has_assertion(source_code: str) -> bool:
    return bool(ASSERTION_PATTERN.search(source_code))


def normalize_signature(text: str) -> str:
    lowered = text.lower().strip()
    lowered = re.sub(r"[^a-z0-9_\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def example_signature(expected_outcome: str, source_code: str, explanation: str) -> str:
    material = "|".join(
        [
            expected_outcome,
            normalize_signature(source_code),
            normalize_signature(explanation),
        ]
    )
    return hashlib.sha1(material.encode("utf-8")).hexdigest()


def classify_rustdoc_observed_outcome(
    expected_outcome: str,
    fence_tag: str,
    return_code: int,
    combined_output: str,
) -> str:
    normalized = normalize_outcome(expected_outcome)
    if normalized == "compile_fail":
        return "compile_fail" if return_code != 0 else "assertion_pass"

    if normalized == "runtime_panic":
        output_lower = combined_output.lower()
        if fence_tag == "should_panic" and return_code == 0:
            return "runtime_panic"
        if return_code != 0 and "panic" in output_lower:
            return "runtime_panic"
        if return_code != 0:
            return "compile_fail"
        return "assertion_pass"

    if return_code == 0:
        return "assertion_pass"
    if "panic" in combined_output.lower():
        return "runtime_panic"
    return "compile_fail"


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
) -> tuple[bool, str, str]:
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
            return False, "expected clippy failure but command succeeded", combined_output
        if lint_id not in combined_output:
            return (
                False,
                f"expected clippy output to reference lint id `{lint_id}`",
                combined_output,
            )
        return True, "", combined_output

    if completed.returncode != 0:
        return False, "expected clippy success but command failed", combined_output
    return True, "", combined_output


def main() -> int:
    args = parse_args()
    root = repo_root()
    todo_path = root / args.todo_guidelines
    payload = load_guidelines_payload(todo_path)
    guidelines = payload.get("guidelines") or []
    policy = read_yaml(root / args.policy) or {}
    work_root = root / args.work_root

    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True, exist_ok=True)

    gate_mode = str(policy.get("gate_mode") or "warn").strip().lower()
    if gate_mode not in {"warn", "error"}:
        gate_mode = "warn"

    require_expected_outcome = bool(policy.get("require_expected_outcome", True))
    require_assertion = bool(policy.get("require_assertion_for_assertion_pass", True))
    documented_only_requires_notes = bool(
        policy.get("documented_only_requires_verification_notes", True)
    )
    thresholds = policy.get("thresholds") or {}

    allowed_outcomes_cfg = policy.get("allowed_outcomes") or {}
    allowed_outcomes = {
        "compliant": {
            normalize_outcome(str(item))
            for item in (
                allowed_outcomes_cfg.get("compliant") or ["assertion_pass", "documented_only"]
            )
            if str(item).strip()
        },
        "non_compliant": {
            normalize_outcome(str(item))
            for item in (
                allowed_outcomes_cfg.get("non_compliant")
                or ["compile_fail", "runtime_panic", "lint_trigger", "documented_only"]
            )
            if str(item).strip()
        },
    }

    errors: list[str] = []
    warnings: list[str] = []
    checked = 0
    example_results: list[dict[str, Any]] = []
    signature_index: dict[str, list[str]] = defaultdict(list)

    def add_policy_finding(message: str) -> None:
        if gate_mode == "error":
            errors.append(message)
        else:
            warnings.append(message)

    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if not guideline_id:
            continue

        decidable_status = str(guideline.get("decidable_status") or "").strip()
        decidable = str(guideline.get("decidable") or "").strip()
        examples = guideline.get("examples") or {}

        for side in ["compliant", "non_compliant"]:
            entry = examples.get(side) or {}
            doc_rel = str(entry.get("doc_path") or "").strip()
            code_rel = str(entry.get("code_path") or "").strip()
            explanation = str(entry.get("explanation") or "").strip()
            compile_expectation = str(entry.get("compile_expectation") or "").strip()
            verification_notes = str(entry.get("verification_notes") or "").strip()
            expected_signals = [
                str(item).strip()
                for item in (entry.get("expected_signals") or [])
                if str(item).strip()
            ]

            raw_expected_outcome = str(entry.get("expected_outcome") or "").strip()
            expected_outcome = normalize_outcome(raw_expected_outcome)
            if not expected_outcome:
                if require_expected_outcome:
                    add_policy_finding(f"{guideline_id}:{side} missing expected_outcome")
                expected_outcome = infer_expected_outcome(
                    side,
                    compile_expectation,
                    decidable_status,
                    decidable,
                )

            if expected_outcome not in VALID_OUTCOMES:
                errors.append(
                    f"{guideline_id}:{side} invalid expected_outcome `{raw_expected_outcome}`"
                )
                expected_outcome = "documented_only"

            if expected_outcome not in allowed_outcomes.get(side, set()):
                add_policy_finding(
                    f"{guideline_id}:{side} expected_outcome `{expected_outcome}` not allowed by policy"
                )

            if (
                expected_outcome == "documented_only"
                and documented_only_requires_notes
                and not verification_notes
            ):
                add_policy_finding(
                    f"{guideline_id}:{side} verification_notes required for documented_only"
                )

            result: dict[str, Any] = {
                "guideline_id": guideline_id,
                "side": side,
                "doc_path": doc_rel,
                "code_path": code_rel,
                "compile_expectation": compile_expectation,
                "expected_outcome": expected_outcome,
                "observed_outcome": "unknown",
                "outcome_match": False,
                "assertion_present": False,
                "signal_match": True,
                "expected_signals": expected_signals,
                "matched_signals": [],
                "negative_evidence_strong": False,
                "verification_notes_present": bool(verification_notes),
            }

            if not doc_rel:
                errors.append(f"{guideline_id}:{side} missing doc_path")
                example_results.append(result)
                continue
            if not code_rel:
                errors.append(f"{guideline_id}:{side} missing code_path")

            doc_path = root / doc_rel
            if not doc_path.exists():
                errors.append(f"{guideline_id}:{side} missing doc file {doc_rel}")
                example_results.append(result)
                continue

            markdown = doc_path.read_text(encoding="utf-8")
            parsed_fence = parse_first_rust_fence(markdown)
            if parsed_fence is None:
                errors.append(f"{guideline_id}:{side} missing Rust fenced block in {doc_rel}")
                example_results.append(result)
                continue

            fence_tag, source_code = parsed_fence
            if code_rel:
                code_path = root / code_rel
                if code_path.exists():
                    source_code = code_path.read_text(encoding="utf-8")

            result["assertion_present"] = has_assertion(source_code)
            signature = example_signature(expected_outcome, source_code, explanation)
            result["signature"] = signature
            signature_index[signature].append(f"{guideline_id}:{side}")

            if compile_expectation and not rustdoc_expectation_matches(
                compile_expectation, fence_tag
            ):
                errors.append(
                    f"{guideline_id}:{side} compile_expectation `{compile_expectation}` does not match fence `{fence_tag}`"
                )

            combined_output = ""

            if expected_outcome == "documented_only":
                observed_outcome = "documented_only"
            elif expected_outcome == "lint_trigger":
                lint_id = str(guideline.get("clippy_lint_id") or "").strip()
                if not lint_id:
                    add_policy_finding(
                        f"{guideline_id}:{side} expected lint_trigger but clippy_lint_id missing"
                    )
                    observed_outcome = "unknown"
                else:
                    harness_dir = work_root / guideline_id / side
                    write_harness(harness_dir / "Cargo.toml", source_code)
                    expect_failure = side == "non_compliant"
                    ok, reason, clippy_output = run_clippy_check(
                        lint_id,
                        harness_dir,
                        expect_failure=expect_failure,
                    )
                    checked += 1
                    combined_output = clippy_output
                    if ok and expect_failure:
                        observed_outcome = "lint_trigger"
                    elif ok:
                        observed_outcome = "assertion_pass"
                    else:
                        observed_outcome = "unknown"
                        add_policy_finding(f"{guideline_id}:{side} clippy check failed: {reason}")
            else:
                completed = run_command(["rustdoc", "--test", str(doc_path)], cwd=root)
                checked += 1
                combined_output = f"{completed.stdout}{completed.stderr}"
                observed_outcome = classify_rustdoc_observed_outcome(
                    expected_outcome,
                    fence_tag,
                    completed.returncode,
                    combined_output,
                )

            result["observed_outcome"] = observed_outcome
            result["outcome_match"] = observed_outcome == expected_outcome

            if (
                expected_outcome == "assertion_pass"
                and require_assertion
                and not result["assertion_present"]
            ):
                add_policy_finding(
                    f"{guideline_id}:{side} assertion_pass expects explicit assertion"
                )

            if expected_signals:
                lowered_output = combined_output.lower()
                matched_signals = [
                    signal for signal in expected_signals if signal.lower() in lowered_output
                ]
                result["matched_signals"] = matched_signals
                result["signal_match"] = len(matched_signals) == len(expected_signals)
                if not result["signal_match"]:
                    add_policy_finding(
                        f"{guideline_id}:{side} expected_signals not found in tool output"
                    )

            if not result["outcome_match"]:
                add_policy_finding(
                    f"{guideline_id}:{side} outcome mismatch expected={expected_outcome} observed={observed_outcome}"
                )

            result["negative_evidence_strong"] = (
                side == "non_compliant" and observed_outcome in NEGATIVE_OUTCOMES
            )

            example_results.append(result)

    total_examples = len(example_results)
    outcome_match_ratio = 1.0
    if total_examples > 0:
        outcome_match_ratio = (
            sum(1 for item in example_results if item["outcome_match"]) / total_examples
        )

    compliant_assertion_examples = [
        item
        for item in example_results
        if item["side"] == "compliant" and item["expected_outcome"] == "assertion_pass"
    ]
    assertion_backed_ratio = 1.0
    if compliant_assertion_examples:
        assertion_backed_ratio = sum(
            1 for item in compliant_assertion_examples if item["assertion_present"]
        ) / len(compliant_assertion_examples)

    non_compliant_examples = [item for item in example_results if item["side"] == "non_compliant"]
    negative_evidence_strength_ratio = 1.0
    if non_compliant_examples:
        negative_evidence_strength_ratio = sum(
            1 for item in non_compliant_examples if item["negative_evidence_strong"]
        ) / len(non_compliant_examples)

    documented_only_ratio = 1.0
    if total_examples > 0:
        documented_only_ratio = (
            sum(1 for item in example_results if item["expected_outcome"] == "documented_only")
            / total_examples
        )

    unique_signature_ratio = 1.0
    if total_examples > 0:
        unique_signature_ratio = len(signature_index) / total_examples

    repeated_signatures = []
    max_repeated_signature_count = int(
        thresholds.get("max_repeated_example_signature_count", 5) or 5
    )
    for signature, members in sorted(
        signature_index.items(), key=lambda item: (-len(item[1]), item[0])
    ):
        if len(members) <= 1:
            continue
        repeated_signatures.append(
            {
                "signature": signature,
                "count": len(members),
                "example_keys": members,
                "violates_policy": len(members) > max_repeated_signature_count,
            }
        )

    diversity_violations = [item for item in repeated_signatures if bool(item["violates_policy"])]

    min_outcome_match_ratio = float(thresholds.get("min_outcome_match_ratio", 0.8) or 0.8)
    min_assertion_ratio = float(thresholds.get("min_assertion_backed_compliant_ratio", 0.6) or 0.6)
    min_negative_ratio = float(thresholds.get("min_negative_evidence_strength_ratio", 0.7) or 0.7)
    max_documented_only_ratio = float(thresholds.get("max_documented_only_ratio", 0.5) or 0.5)
    min_unique_signature_ratio = float(
        thresholds.get("min_unique_example_signature_ratio", 0.3) or 0.3
    )

    if outcome_match_ratio + 1e-9 < min_outcome_match_ratio:
        add_policy_finding(
            f"outcome_match_ratio {outcome_match_ratio:.3f} < threshold {min_outcome_match_ratio:.3f}"
        )
    if assertion_backed_ratio + 1e-9 < min_assertion_ratio:
        add_policy_finding(
            "assertion_backed_compliant_ratio "
            f"{assertion_backed_ratio:.3f} < threshold {min_assertion_ratio:.3f}"
        )
    if negative_evidence_strength_ratio + 1e-9 < min_negative_ratio:
        add_policy_finding(
            "negative_evidence_strength_ratio "
            f"{negative_evidence_strength_ratio:.3f} < threshold {min_negative_ratio:.3f}"
        )
    if documented_only_ratio - 1e-9 > max_documented_only_ratio:
        add_policy_finding(
            f"documented_only_ratio {documented_only_ratio:.3f} > threshold {max_documented_only_ratio:.3f}"
        )
    if unique_signature_ratio + 1e-9 < min_unique_signature_ratio:
        add_policy_finding(
            f"unique_example_signature_ratio {unique_signature_ratio:.3f} < threshold {min_unique_signature_ratio:.3f}"
        )

    for violation in diversity_violations:
        add_policy_finding(
            "repeated example signature exceeds limit "
            f"count={violation['count']} examples={','.join(violation['example_keys'])}"
        )

    metrics: dict[str, Any] = {
        "outcome_match_ratio": round(outcome_match_ratio, 6),
        "assertion_backed_compliant_ratio": round(assertion_backed_ratio, 6),
        "negative_evidence_strength_ratio": round(negative_evidence_strength_ratio, 6),
        "documented_only_ratio": round(documented_only_ratio, 6),
        "unique_example_signature_ratio": round(unique_signature_ratio, 6),
        "repeated_signature_violation_count": len(diversity_violations),
    }

    report: dict[str, Any] = {
        "checked_examples": checked,
        "example_count": total_examples,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "ok": len(errors) == 0,
        "gate_mode": gate_mode,
        "metrics": metrics,
        "example_results": example_results,
        "repeated_signatures": repeated_signatures,
        "diversity_violations": diversity_violations,
    }

    if args.json_output:
        write_json(args.json_output, report)

    if errors:
        print(f"[guideline-examples] failed with {len(errors)} error(s)")
        for error in errors:
            print(f"[guideline-examples][error] {error}")
        return EXIT_POLICY_FAIL

    print(
        "[guideline-examples] "
        f"ok checked={checked} warnings={len(warnings)} "
        f"outcome_match={metrics['outcome_match_ratio']:.3f} "
        f"assertion_ratio={metrics['assertion_backed_compliant_ratio']:.3f} "
        f"negative_ratio={metrics['negative_evidence_strength_ratio']:.3f}"
    )
    for warning in warnings:
        print(f"[guideline-examples][warn] {warning}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FileNotFoundError as exc:
        print(f"[guideline-examples][error] runtime dependency missing: {exc}")
        sys.exit(EXIT_RUNTIME_FAIL)
