#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from _common import (
    EXIT_POLICY_FAIL,
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    load_guidelines_payload,
    read_json,
    read_yaml,
    repo_root,
    write_json,
)
from known_good_lib import cosine_similarity, extract_feature_vector, utc_now


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare local guidelines against known-good baseline"
    )
    parser.add_argument("--todo-guidelines", type=Path, default=Path("data/todo_guidelines.yaml"))
    parser.add_argument(
        "--alignment-policy", type=Path, default=Path("config/alignment_policy.yaml")
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--canonical-dir", type=Path)
    parser.add_argument("--changed-guidelines", type=Path)
    parser.add_argument("--allow-missing-benchmark", action="store_true")
    parser.add_argument("--enforce", action="store_true")
    parser.add_argument("--gate-mode", choices=["warn", "error"])
    parser.add_argument("--min-global-alignment", type=float)
    parser.add_argument("--min-changed-guideline-alignment", type=float)
    parser.add_argument("--granularity-outliers-allowed", type=int)
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def parse_markdown_example(markdown: str) -> tuple[str, str]:
    description_lines: list[str] = []
    rust_lines: list[str] = []
    in_code = False
    for line in markdown.splitlines():
        if line.strip().startswith("```rust"):
            in_code = True
            continue
        if line.strip().startswith("```") and in_code:
            in_code = False
            continue
        if in_code:
            rust_lines.append(line.rstrip())
            continue
        if line.startswith("#"):
            continue
        description_lines.append(line.rstrip())

    description = "\n".join(description_lines).strip()
    rust_code = "\n".join(rust_lines).rstrip()
    return description, rust_code


def build_local_canonical(guideline: dict[str, Any], root: Path) -> dict[str, Any]:
    examples = guideline.get("examples") or {}
    compliant_examples = []
    non_compliant_examples = []

    for side, collection in [
        ("compliant", compliant_examples),
        ("non_compliant", non_compliant_examples),
    ]:
        entry = examples.get(side) or {}
        doc_rel = str(entry.get("doc_path") or "").strip()
        description = str(entry.get("explanation") or "")
        rust_code = ""
        if doc_rel:
            doc_path = root / doc_rel
            if doc_path.exists():
                markdown = doc_path.read_text(encoding="utf-8")
                parsed_description, parsed_code = parse_markdown_example(markdown)
                if parsed_description:
                    description = parsed_description
                rust_code = parsed_code

        collection.append(
            {
                "example_id": f"{guideline.get('id', 'RG-UNKNOWN')}-{side}",
                "status": "",
                "description": description,
                "rust_code": rust_code,
                "options": {
                    "compile_expectation": str(entry.get("compile_expectation") or ""),
                },
            }
        )

    rule_text = "\n\n".join(
        [
            str(guideline.get("rule_statement") or ""),
            str(guideline.get("amplification") or ""),
            str(guideline.get("exceptions") or ""),
        ]
    ).strip()
    rationale_text = "\n\n".join(
        [
            str(guideline.get("rationale") or ""),
            str(guideline.get("decidability_rationale") or ""),
        ]
    ).strip()

    citations = re.findall(r":cite:`([^`]+)`", rule_text + "\n" + rationale_text)
    std_refs = re.findall(r":std:`([^`]+)`", rule_text + "\n" + rationale_text)
    references = [
        {
            "key": f"fls:{ref}",
            "description": f"Mapped FLS reference {ref}",
        }
        for ref in guideline.get("fls_refs", [])
        if str(ref).strip()
    ]

    return {
        "rule_text": rule_text,
        "rationale_text": rationale_text,
        "compliant_examples": compliant_examples,
        "non_compliant_examples": non_compliant_examples,
        "references": references,
        "citations": sorted(set(citations)),
        "std_refs": sorted(set(std_refs)),
    }


def score_feature(value: float, stats: dict[str, float]) -> float:
    p10 = float(stats.get("p10", 0.0))
    p25 = float(stats.get("p25", 0.0))
    p75 = float(stats.get("p75", 0.0))
    p90 = float(stats.get("p90", 0.0))

    if p25 <= value <= p75:
        return 1.0

    if value <= p10:
        if p10 == p25:
            return 0.0
        return max(0.0, min(1.0, (value - p10) / (p25 - p10)))

    if value >= p90:
        if p90 == p75:
            return 0.0
        return max(0.0, min(1.0, (p90 - value) / (p90 - p75)))

    if value < p25:
        if p25 == p10:
            return 0.0
        return max(0.0, min(1.0, (value - p10) / (p25 - p10)))

    if p90 == p75:
        return 0.0
    return max(0.0, min(1.0, (p90 - value) / (p90 - p75)))


def average(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


TOPIC_BUCKET_KEYWORDS: dict[str, set[str]] = {
    "defensive": {
        "defensive",
        "error",
        "errors",
        "result",
        "option",
        "panic",
        "unwrap",
        "expect",
    },
    "subset": {
        "subset",
        "unsafe",
        "forbidden",
        "forbid",
        "pointer",
        "raw",
        "language",
        "construct",
    },
    "complexity": {
        "complexity",
        "traceable",
        "reviewable",
        "structure",
        "bounded",
    },
    "type": {
        "type",
        "types",
        "conversion",
        "tryfrom",
        "trait",
        "traits",
    },
    "concurrency": {
        "concurrency",
        "thread",
        "atomic",
        "mutex",
        "lock",
        "shared",
        "race",
    },
}


def infer_topic_bucket(text: str) -> str:
    lowered = text.lower()
    tokens = set(re.findall(r"[a-z0-9_]+", lowered))
    best_bucket = "misc"
    best_score = 0
    for bucket, keywords in TOPIC_BUCKET_KEYWORDS.items():
        score = len(tokens & keywords)
        if score > best_score:
            best_score = score
            best_bucket = bucket
    return best_bucket


def nearest_neighbors(
    local_text: str,
    benchmark_records: list[dict[str, Any]],
    local_topic_bucket: str,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    topic_matched = [
        record
        for record in benchmark_records
        if str(record.get("_topic_bucket") or "") == local_topic_bucket
    ]
    candidate_pool = topic_matched if len(topic_matched) >= max(1, top_k) else benchmark_records

    scored = []
    for record in candidate_pool:
        bench_text = str(record.get("rule_text") or "") + "\n" + str(
            record.get("rationale_text") or ""
        )
        similarity = cosine_similarity(local_text, bench_text)
        scored.append(
            {
                "guideline_id": str(record.get("guideline_id") or ""),
                "similarity": round(similarity, 6),
            }
        )
    scored.sort(key=lambda item: (-item["similarity"], item["guideline_id"]))
    return scored[:top_k]


def load_changed_set(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    values = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if item:
            values.add(item)
    return values


def main() -> int:
    args = parse_args()
    root = repo_root()

    policy_path = root / args.alignment_policy
    todo_path = root / args.todo_guidelines
    if not policy_path.exists():
        print(f"[known-good-alignment][error] missing policy: {policy_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL
    if not todo_path.exists():
        print(f"[known-good-alignment][error] missing guidelines: {todo_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL

    policy = read_yaml(policy_path) or {}
    benchmark_paths = policy.get("benchmark_paths") or {}
    thresholds = dict(policy.get("thresholds") or {})
    if args.min_global_alignment is not None:
        thresholds["min_global_alignment"] = float(args.min_global_alignment)
    if args.min_changed_guideline_alignment is not None:
        thresholds["min_changed_guideline_alignment"] = float(
            args.min_changed_guideline_alignment
        )
    if args.granularity_outliers_allowed is not None:
        thresholds["granularity_outliers_allowed"] = int(args.granularity_outliers_allowed)

    gate_mode = str(args.gate_mode or policy.get("gate_mode") or "warn")

    manifest_default = Path(
        str(benchmark_paths.get("manifest_path") or "benchmarks/known-good/manifest.yaml")
    )
    baseline_default = Path(
        str(benchmark_paths.get("baseline_path") or "benchmarks/known-good/features/baseline.json")
    )
    manifest_path = root / (args.manifest or manifest_default)
    baseline_path = root / (args.baseline or baseline_default)
    canonical_dir = root / (
        args.canonical_dir
        or Path(str(benchmark_paths.get("canonical_dir") or "benchmarks/known-good/canonical"))
    )

    missing_benchmark_inputs = [
        path
        for path in [manifest_path, baseline_path, canonical_dir]
        if not path.exists()
    ]
    if missing_benchmark_inputs:
        report = {
            "version": 1,
            "generated_at": utc_now(),
            "benchmark_pack_id": "missing",
            "active_tier": str(policy.get("active_tier") or "strict"),
            "gate_mode": gate_mode,
            "thresholds": thresholds,
            "guideline_count": 0,
            "average_alignment_score": 0.0,
            "warning_count": 1,
            "error_count": 0,
            "guideline_results": [],
            "warnings": [
                "benchmark inputs missing: "
                + ", ".join(str(path.relative_to(root)) for path in missing_benchmark_inputs)
            ],
            "errors": [],
            "ok": True,
        }
        if args.json_output:
            write_json(args.json_output, report)

        if args.allow_missing_benchmark:
            print("[known-good-alignment] benchmark missing, skipping in allow-missing mode")
            return EXIT_SUCCESS

        print("[known-good-alignment][error] benchmark inputs missing")
        return EXIT_RUNTIME_FAIL

    manifest = read_yaml(manifest_path) or {}
    baseline = read_json(baseline_path)
    feature_stats = baseline.get("feature_stats") or {}
    weights = policy.get("weights") or baseline.get("dimension_weights") or {}
    active_tier = str(policy.get("active_tier") or baseline.get("active_tier") or "strict")
    hard_fail = args.enforce or gate_mode == "error"

    benchmark_records = []
    allowed_tiers = {"strict"} if active_tier == "strict" else {"strict", "extended"}
    if active_tier == "all":
        allowed_tiers = {"strict", "extended"}

    for entry in manifest.get("guidelines", []):
        tier = str(entry.get("tier") or "extended")
        if tier not in allowed_tiers:
            continue
        canonical_rel = str(entry.get("local_canonical_path") or "").strip()
        if not canonical_rel:
            continue
        canonical_path = root / canonical_rel
        if not canonical_path.exists():
            continue
        canonical = read_json(canonical_path)
        benchmark_topic_source = "\n".join(
            [
                str(canonical.get("rule_text") or ""),
                str(canonical.get("rationale_text") or ""),
                " ".join(str(item) for item in (canonical.get("metadata") or {}).get("tags", [])),
            ]
        )
        canonical["_topic_bucket"] = infer_topic_bucket(benchmark_topic_source)
        benchmark_records.append(canonical)

    if not benchmark_records:
        print("[known-good-alignment][error] no benchmark canonical records found")
        return EXIT_RUNTIME_FAIL

    changed_path = root / args.changed_guidelines if args.changed_guidelines else None
    changed_set = load_changed_set(changed_path)

    guidelines = load_guidelines_payload(todo_path).get("guidelines") or []
    guideline_results = []
    errors: list[str] = []
    warnings: list[str] = []

    outlier_count = 0
    advisory_similarity = policy.get("advisory_similarity") or {}
    use_similarity = bool(advisory_similarity.get("enabled", True))
    min_similarity = float(advisory_similarity.get("min_similarity", 0.70))
    dimension_floors = policy.get("dimension_floors") or {}
    floor_dimensions = ["structure", "specificity", "evidence", "citations", "granularity"]
    apply_floors_to_changed = bool(dimension_floors.get("apply_to_changed_guidelines", True))
    floor_by_dimension = {
        dimension: float(dimension_floors.get(dimension, 0.0) or 0.0)
        for dimension in floor_dimensions
    }

    dimension_features = {
        "structure": [
            "section_count",
            "rationale_present",
            "compliant_examples_count",
            "non_compliant_examples_count",
        ],
        "specificity": [
            "rust_term_density",
            "constraint_phrase_density",
            "rule_word_count",
        ],
        "evidence": [
            "example_code_block_count",
            "example_avg_explanation_words",
            "example_diversity",
            "code_token_total",
        ],
        "citations": ["citation_count", "bibliography_present"],
        "granularity": [
            "concepts_per_100_words",
            "conditions_per_100_words",
            "examples_per_concept",
            "concept_count",
        ],
    }

    weight_total = sum(float(weights.get(name, 0.0)) for name in dimension_features)
    if weight_total <= 0:
        weight_total = 1.0

    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if not guideline_id:
            continue

        local_canonical = build_local_canonical(guideline, root)
        feature_values = extract_feature_vector(local_canonical)
        local_topic_bucket = infer_topic_bucket(
            "\n".join(
                [
                    str(guideline.get("technical_topic") or ""),
                    str(local_canonical.get("rule_text") or ""),
                    str(local_canonical.get("rationale_text") or ""),
                ]
            )
        )

        dimension_scores: dict[str, float] = {}
        for dimension, feature_names in dimension_features.items():
            feature_scores = []
            for feature_name in feature_names:
                stats = feature_stats.get(feature_name)
                if not isinstance(stats, dict):
                    continue
                value = float(feature_values.get(feature_name, 0.0))
                feature_scores.append(score_feature(value, stats))
            dimension_scores[dimension] = round(average(feature_scores), 6)

        alignment_score = 0.0
        for dimension, value in dimension_scores.items():
            dimension_weight = float(weights.get(dimension, 0.0))
            alignment_score += value * dimension_weight
        alignment_score = round(alignment_score / weight_total, 6)

        is_changed_guideline = (not changed_set) or guideline_id in changed_set
        apply_dimension_floors = True
        if apply_floors_to_changed:
            apply_dimension_floors = is_changed_guideline

        flags = []
        min_changed = float(thresholds.get("min_changed_guideline_alignment", 0.80))
        if alignment_score < min_changed:
            flags.append("known_good_alignment_gap")

        if apply_dimension_floors:
            floor_violated = False
            for dimension, floor in floor_by_dimension.items():
                if floor <= 0:
                    continue
                value = float(dimension_scores.get(dimension, 0.0))
                if value + 1e-9 < floor:
                    flags.append(f"dimension_floor_{dimension}")
                    floor_violated = True
            if floor_violated:
                flags.append("known_good_alignment_gap")

        concept_stats = feature_stats.get("concept_count") or {}
        conditions_stats = feature_stats.get("conditions_per_100_words") or {}
        examples_total_stats = feature_stats.get("examples_total") or {}
        if (
            float(feature_values.get("concept_count", 0.0)) < float(concept_stats.get("p10", 0.0))
            and float(feature_values.get("examples_total", 0.0))
            < float(examples_total_stats.get("p25", 0.0))
        ):
            flags.append("granularity_too_coarse")

        if (
            float(feature_values.get("concept_count", 0.0)) > float(concept_stats.get("p90", 0.0))
            or float(feature_values.get("conditions_per_100_words", 0.0))
            > float(conditions_stats.get("p90", 0.0))
        ):
            flags.append("granularity_too_fine")

        if (
            float(feature_values.get("example_avg_explanation_words", 0.0))
            < float((feature_stats.get("example_avg_explanation_words") or {}).get("p10", 0.0))
            or float(feature_values.get("example_code_block_count", 0.0))
            < float((feature_stats.get("example_code_block_count") or {}).get("p25", 0.0))
        ):
            flags.append("example_depth_too_shallow")

        if float(feature_values.get("citation_count", 0.0)) < float(
            (feature_stats.get("citation_count") or {}).get("p25", 0.0)
        ):
            flags.append("citation_coverage_low")

        nearest = []
        if use_similarity:
            local_text = local_canonical["rule_text"] + "\n" + local_canonical["rationale_text"]
            nearest = nearest_neighbors(
                local_text,
                benchmark_records,
                local_topic_bucket=local_topic_bucket,
                top_k=3,
            )
            if nearest and nearest[0]["similarity"] < min_similarity:
                flags.append("benchmark_similarity_gap")

        granularity_flags = {"granularity_too_coarse", "granularity_too_fine"}
        if any(flag in granularity_flags for flag in flags):
            outlier_count += 1

        guideline_result = {
            "guideline_id": guideline_id,
            "alignment_score": alignment_score,
            "dimension_scores": dimension_scores,
            "feature_values": {
                key: round(float(value), 6) for key, value in sorted(feature_values.items())
            },
            "flags": sorted(set(flags)),
            "nearest_neighbors": nearest,
        }
        guideline_results.append(guideline_result)

        threshold_to_use = min_changed if not changed_set or guideline_id in changed_set else float(
            thresholds.get("min_global_alignment", 0.75)
        )
        if alignment_score < threshold_to_use:
            message = (
                f"{guideline_id} alignment score {alignment_score:.3f} "
                f"< threshold {threshold_to_use:.3f}"
            )
            if hard_fail:
                errors.append(message)
            else:
                warnings.append(message)

    guideline_results.sort(key=lambda item: item["guideline_id"])
    average_alignment = average([item["alignment_score"] for item in guideline_results])
    min_global = float(thresholds.get("min_global_alignment", 0.75))

    if average_alignment < min_global:
        message = f"global alignment {average_alignment:.3f} < threshold {min_global:.3f}"
        if hard_fail:
            errors.append(message)
        else:
            warnings.append(message)

    allowed_outliers = int(thresholds.get("granularity_outliers_allowed", 0))
    if outlier_count > allowed_outliers:
        message = (
            f"granularity outliers {outlier_count} > allowed {allowed_outliers}"
        )
        if hard_fail:
            errors.append(message)
        else:
            warnings.append(message)

    report = {
        "version": 1,
        "generated_at": utc_now(),
        "benchmark_pack_id": str(baseline.get("source_pack_id") or "unknown-pack"),
        "active_tier": active_tier,
        "gate_mode": "error" if hard_fail else "warn",
        "thresholds": thresholds,
        "guideline_count": len(guideline_results),
        "average_alignment_score": round(average_alignment, 6),
        "warning_count": len(warnings),
        "error_count": len(errors),
        "guideline_results": guideline_results,
        "warnings": warnings,
        "errors": errors,
        "ok": len(errors) == 0,
    }

    if args.json_output:
        write_json(args.json_output, report)

    if errors:
        print(f"[known-good-alignment] failed with {len(errors)} error(s)")
        for error in errors:
            print(f"[known-good-alignment][error] {error}")
        return EXIT_POLICY_FAIL

    print(
        "[known-good-alignment] "
        f"ok guidelines={len(guideline_results)} avg_alignment={average_alignment:.3f} "
        f"warnings={len(warnings)}"
    )
    for warning in warnings:
        print(f"[known-good-alignment][warn] {warning}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
