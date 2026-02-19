#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import (
    EXIT_POLICY_FAIL,
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    load_guidelines_payload,
    read_yaml,
    repo_root,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check guideline editorial/technical quality thresholds"
    )
    parser.add_argument("--todo-guidelines", type=Path, default=Path("data/todo_guidelines.yaml"))
    parser.add_argument("--policy", type=Path, default=Path("config/completeness_policy.yaml"))
    parser.add_argument("--enforce", action="store_true")
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def contains_term(value: str, terms: list[str]) -> bool:
    lowered = value.lower()
    return any(term in lowered for term in terms)


def main() -> int:
    args = parse_args()
    root = repo_root()
    todo_path = root / args.todo_guidelines
    policy_path = root / args.policy

    if not todo_path.exists():
        print(f"[guideline-quality][error] missing guidelines: {todo_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL
    if not policy_path.exists():
        print(f"[guideline-quality][error] missing policy: {policy_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL

    payload = load_guidelines_payload(todo_path)
    policy = read_yaml(policy_path) or {}
    quality_policy = policy.get("quality") or {}

    min_score = int(quality_policy.get("min_score", 70))
    placeholder_terms = [
        str(item).strip().lower()
        for item in quality_policy.get("placeholder_terms", [])
        if str(item).strip()
    ]
    if not placeholder_terms:
        placeholder_terms = ["placeholder", "pending", "todo"]

    hard_fail = (
        args.enforce
        or str((policy.get("gate_modes") or {}).get("guideline_quality") or "warn") == "error"
    )

    warnings: list[str] = []
    errors: list[str] = []
    guideline_scores = []

    for guideline in payload.get("guidelines", []):
        guideline_id = str(guideline.get("id") or "").strip()
        if not guideline_id:
            continue

        score = 100
        findings = []

        for field in ["rule_statement", "amplification", "exceptions", "rationale"]:
            text = str(guideline.get(field) or "")
            if not text.strip():
                score -= 30
                findings.append(f"missing {field}")
                continue
            if contains_term(text, placeholder_terms):
                score -= 30
                findings.append(f"placeholder-like text in {field}")

        rule_statement = str(guideline.get("rule_statement") or "")
        if len(rule_statement.strip()) < 30:
            score -= 10
            findings.append("rule_statement too short")

        fls_refs = [
            str(item).strip() for item in guideline.get("fls_refs", []) if str(item).strip()
        ]
        if not fls_refs:
            score -= 40
            findings.append("missing fls_refs")

        examples = guideline.get("examples") or {}
        for side in ["compliant", "non_compliant"]:
            doc_rel = str((examples.get(side) or {}).get("doc_path") or "").strip()
            if not doc_rel:
                score -= 10
                findings.append(f"missing {side} doc_path")
                continue

            doc_path = root / doc_rel
            if not doc_path.exists():
                score -= 10
                findings.append(f"missing {side} doc file")
                continue

            markdown = doc_path.read_text(encoding="utf-8")
            if contains_term(markdown, placeholder_terms):
                score -= 15
                findings.append(f"placeholder-like text in {side} example markdown")

        score = max(score, 0)
        guideline_scores.append(
            {"guideline_id": guideline_id, "score": score, "findings": findings}
        )

        if score < min_score:
            message = f"{guideline_id} quality score {score} < minimum {min_score}"
            if hard_fail:
                errors.append(message)
            else:
                warnings.append(message)

    avg_score = 0.0
    if guideline_scores:
        avg_score = sum(item["score"] for item in guideline_scores) / len(guideline_scores)

    report = {
        "guideline_count": len(guideline_scores),
        "min_score": min_score,
        "average_score": round(avg_score, 2),
        "gate_mode": "error" if hard_fail else "warn",
        "error_count": len(errors),
        "warning_count": len(warnings),
        "guideline_scores": sorted(guideline_scores, key=lambda item: item["guideline_id"]),
        "errors": errors,
        "warnings": warnings,
        "ok": len(errors) == 0,
    }

    if args.json_output:
        write_json(args.json_output, report)

    if errors:
        print(f"[guideline-quality] failed with {len(errors)} error(s)")
        for error in errors:
            print(f"[guideline-quality][error] {error}")
        return EXIT_POLICY_FAIL

    print(
        "[guideline-quality] "
        f"ok guidelines={report['guideline_count']} avg_score={report['average_score']} "
        f"warnings={report['warning_count']}"
    )
    for warning in warnings:
        print(f"[guideline-quality][warn] {warning}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
