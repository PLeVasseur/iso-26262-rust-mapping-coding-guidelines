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
        ("schemas/completeness_policy.schema.json", "config/completeness_policy.yaml", True),
        (
            "schemas/citation_grounding_policy.schema.json",
            "config/citation_grounding_policy.yaml",
            True,
        ),
        ("schemas/diversity_policy.schema.json", "config/diversity_policy.yaml", True),
        ("schemas/known_good_policy.schema.json", "config/known_good_policy.yaml", True),
        ("schemas/alignment_policy.schema.json", "config/alignment_policy.yaml", True),
        (
            "schemas/rust_signal_sources.schema.json",
            "config/rust_signal_sources.yaml",
            True,
        ),
        (
            "schemas/controller_decision_policy.schema.json",
            "config/controller_decision_policy.yaml",
            True,
        ),
        (
            "schemas/controller_rewrite_policy.schema.json",
            "config/controller_rewrite_policy.yaml",
            True,
        ),
        ("schemas/seed_manifest.schema.json", "seeds/seed_manifest.yaml", True),
        ("schemas/run_registry.schema.json", "data/run_registry.yaml", True),
        ("schemas/clippy_lints_catalog.schema.json", "data/clippy_lints_catalog.yaml", True),
        ("schemas/target_scope.schema.json", "data/target_scope.yaml", True),
        ("schemas/extractor_findings.schema.json", "feedback/extractor_findings.yaml", True),
        ("schemas/seed_topics.schema.json", "data/seed_topics.yaml", args.strict_generated),
        ("schemas/todo_guidelines.schema.json", "data/todo_guidelines.yaml", args.strict_generated),
        ("schemas/fls_inventory.schema.json", "data/fls_inventory.yaml", args.strict_generated),
        (
            "schemas/fls_target_candidates.schema.json",
            "data/fls_target_candidates.yaml",
            args.strict_generated,
        ),
        (
            "schemas/decomposition_report.schema.json",
            "data/decomposition_report.yaml",
            args.strict_generated,
        ),
        (
            "schemas/rust_signals.schema.json",
            "data/rust_signals.yaml",
            args.strict_generated,
        ),
        (
            "schemas/known_good_manifest.schema.json",
            "benchmarks/known-good/manifest.yaml",
            args.strict_generated,
        ),
        (
            "schemas/known_good_features.schema.json",
            "benchmarks/known-good/features/baseline.json",
            args.strict_generated,
        ),
        (
            "schemas/known_good_alignment_report.schema.json",
            "benchmarks/known-good/reports/alignment_report.json",
            args.strict_generated,
        ),
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

    review_schema_path = root / "schemas" / "diffset_review.schema.json"
    required_schema_files = [
        "schemas/completeness_policy.schema.json",
        "schemas/clippy_lints_catalog.schema.json",
        "schemas/controller_blocker_report.schema.json",
        "schemas/controller_delta_summary.schema.json",
        "schemas/controller_decision_packet.schema.json",
        "schemas/controller_decision_policy.schema.json",
        "schemas/controller_llm_rewrite.schema.json",
        "schemas/controller_rewrite_policy.schema.json",
        "schemas/controller_handoff.schema.json",
        "schemas/controller_iteration.schema.json",
        "schemas/controller_llm_decision.schema.json",
        "schemas/controller_lane_status.schema.json",
        "schemas/controller_run_registry_candidate.schema.json",
        "schemas/controller_state.schema.json",
        "schemas/controller_supervisor_state.schema.json",
        "schemas/citation_grounding_policy.schema.json",
        "schemas/decomposition_report.schema.json",
        "schemas/diffset_manifest.schema.json",
        "schemas/diffset_item.schema.json",
        "schemas/diffset_review.schema.json",
        "schemas/diversity_policy.schema.json",
        "schemas/fls_inventory.schema.json",
        "schemas/fls_target_candidates.schema.json",
        "schemas/alignment_policy.schema.json",
        "schemas/guideline_diversity_report.schema.json",
        "schemas/known_good_alignment_report.schema.json",
        "schemas/known_good_canonical.schema.json",
        "schemas/known_good_features.schema.json",
        "schemas/known_good_manifest.schema.json",
        "schemas/known_good_policy.schema.json",
        "schemas/rust_signal_sources.schema.json",
        "schemas/rust_signals.schema.json",
    ]
    for rel_path in required_schema_files:
        schema_path = root / rel_path
        if not schema_path.exists():
            failures.append({"file": rel_path, "errors": ["schema file missing"]})

    review_dir = root / "feedback" / "diffset_reviews"
    if review_schema_path.exists():
        review_files = sorted(list(review_dir.glob("*.yaml")) + list(review_dir.glob("*.yml")))
        if review_files:
            for review_file in review_files:
                errors = validate_pair(review_schema_path, review_file)
                if errors:
                    failures.append(
                        {
                            "file": str(review_file.relative_to(root)),
                            "errors": errors,
                        }
                    )
                else:
                    validated += 1
        else:
            skipped += 1

    canonical_schema_path = root / "schemas" / "known_good_canonical.schema.json"
    canonical_dir = root / "benchmarks" / "known-good" / "canonical"
    if canonical_schema_path.exists():
        canonical_files = sorted(canonical_dir.glob("*.json"))
        if canonical_files:
            for canonical_file in canonical_files:
                errors = validate_pair(canonical_schema_path, canonical_file)
                if errors:
                    failures.append(
                        {
                            "file": str(canonical_file.relative_to(root)),
                            "errors": errors,
                        }
                    )
                else:
                    validated += 1
        elif args.strict_generated:
            failures.append(
                {
                    "file": "benchmarks/known-good/canonical",
                    "errors": ["no canonical benchmark files found"],
                }
            )
        else:
            skipped += 1

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
