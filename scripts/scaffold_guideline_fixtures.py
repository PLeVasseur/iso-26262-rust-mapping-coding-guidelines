#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import EXIT_SUCCESS, read_yaml, repo_root, write_yaml

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


def main() -> int:
    args = parse_args()
    root = repo_root()
    backlog_path = root / args.todo_guidelines
    tests_root = root / args.tests_root

    payload = read_yaml(backlog_path) or {}
    guidelines = payload.get("guidelines") or []

    created = 0
    for guideline in guidelines:
        rule_id = str(guideline.get("id") or "").strip()
        if not rule_id:
            continue

        mode = str(guideline.get("enforcement_mode") or "AUDIT").strip().upper()
        rule_dir = tests_root / rule_id
        rule_dir.mkdir(parents=True, exist_ok=True)

        metadata_path = rule_dir / "metadata.yaml"
        metadata_payload = {
            "version": 1,
            "rule_id": rule_id,
            "category": guideline.get("category"),
            "mode": mode,
            "state": guideline.get("state", "DRAFT"),
            "iso_seeds": guideline.get("iso_seeds") or [],
            "pass_criteria": "No violation reported for compliant fixture.",
            "fail_criteria": "Violation reported for violating fixture.",
        }
        if not metadata_path.exists() or args.overwrite:
            write_yaml(metadata_path, metadata_payload)

        for rel_path, content in AUTO_FILES.items():
            if mode in {"AUTO", "HYBRID"}:
                write_text_file(rule_dir / rel_path, content, args.overwrite)

        for rel_path, content in AUDIT_FILES.items():
            if mode in {"AUDIT", "HYBRID"}:
                write_text_file(rule_dir / rel_path, content, args.overwrite)

        for rel_path, content in HYBRID_FILES.items():
            if mode == "HYBRID":
                write_text_file(rule_dir / rel_path, content, args.overwrite)

        created += 1

    print(f"[fixture-scaffold] processed rules={created} -> {tests_root.relative_to(root)}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
