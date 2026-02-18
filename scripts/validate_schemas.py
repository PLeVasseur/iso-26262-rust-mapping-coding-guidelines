#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from _common import EXIT_POLICY_FAIL, EXIT_SUCCESS, read_json, read_yaml, repo_root, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate config/data files against JSON schemas")
    parser.add_argument(
        "--strict-generated",
        action="store_true",
        help="Fail when generated files are missing",
    )
    parser.add_argument("--json-output", type=Path, help="Optional JSON report output path")
    return parser.parse_args()


def load_document(path: Path):
    if path.suffix in {".yaml", ".yml"}:
        return read_yaml(path)
    return read_json(path)


def validate_pair(schema_path: Path, payload_path: Path) -> list[str]:
    schema = read_json(schema_path)
    payload = load_document(payload_path)
    validator = Draft202012Validator(schema)
    return [error.message for error in validator.iter_errors(payload)]


def main() -> int:
    args = parse_args()
    root = repo_root()

    checks = [
        ("schemas/extractor_paths.schema.json", "config/extractor_paths.yaml", True),
        ("schemas/corpus_registry.schema.json", "config/corpus_registry.yaml", True),
        ("schemas/change_growth_policy.schema.json", "config/change_growth_policy.yaml", True),
        ("schemas/seed_manifest.schema.json", "seeds/seed_manifest.yaml", True),
        ("schemas/run_registry.schema.json", "data/run_registry.yaml", True),
        ("schemas/target_scope.schema.json", "data/target_scope.yaml", True),
        ("schemas/extractor_findings.schema.json", "feedback/extractor_findings.yaml", True),
        ("schemas/seed_topics.schema.json", "data/seed_topics.yaml", args.strict_generated),
        ("schemas/todo_guidelines.schema.json", "data/todo_guidelines.yaml", args.strict_generated),
    ]

    failures: list[dict[str, object]] = []
    validated = 0
    skipped = 0

    for schema_rel, payload_rel, required in checks:
        schema_path = root / schema_rel
        payload_path = root / payload_rel

        if not schema_path.exists():
            failures.append({"file": schema_rel, "errors": ["schema file missing"]})
            continue

        if not payload_path.exists():
            if required:
                failures.append({"file": payload_rel, "errors": ["payload file missing"]})
            else:
                skipped += 1
            continue

        errors = validate_pair(schema_path, payload_path)
        if errors:
            failures.append({"file": payload_rel, "errors": errors})
        else:
            validated += 1

    report = {
        "validated": validated,
        "skipped": skipped,
        "failure_count": len(failures),
        "failures": failures,
        "ok": len(failures) == 0,
    }

    if args.json_output:
        write_json(args.json_output, report)

    if report["ok"]:
        print(f"[schema] validated={validated} skipped={skipped}")
        return EXIT_SUCCESS

    print(f"[schema] failures={len(failures)}")
    for failure in failures:
        print(f"[schema][error] {failure['file']}: {failure['errors']}")
    return EXIT_POLICY_FAIL


if __name__ == "__main__":
    sys.exit(main())
