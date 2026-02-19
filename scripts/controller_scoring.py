from __future__ import annotations

from typing import Any


def metric_vector(observation: dict[str, Any]) -> tuple[float, ...]:
    runtime_failures = float(observation.get("runtime_failures", 0))
    policy_failures = float(observation.get("policy_failures", 0))
    iso_obligation_gap_count = float(observation.get("iso_obligation_gap_count", 0))
    traceability_gap_count = float(observation.get("traceability_gap_count", 0))
    target_fanout_gap_count = float(observation.get("target_fanout_gap_count", 0))
    fls_gap_total = float(observation.get("fls_span_gap_count", 0)) + float(
        observation.get("fls_chapter_gap_count", 0)
    )
    quality_gap_total = (
        float(observation.get("quality_gap_count", 0))
        + float(observation.get("placeholder_gap_count", 0))
        + float(observation.get("example_gap_count", 0))
        + float(observation.get("example_outcome_gap_count", 0))
        + float(observation.get("example_assertion_gap_count", 0))
        + float(observation.get("example_negative_evidence_gap_count", 0))
        + float(observation.get("example_diversity_gap_count", 0))
        + float(observation.get("known_good_alignment_gap_count", 0))
        + float(observation.get("duplication_gap_count", 0))
        + float(observation.get("duplication_exception_missing_count", 0))
        + float(observation.get("rust_signal_gap_count", 0))
    )
    iso_obligation_coverage = float(observation.get("iso_obligation_coverage", 0.0))
    fls_chapter_coverage = float(observation.get("fls_chapter_coverage", 0.0))
    quality_pass_ratio = float(observation.get("quality_pass_ratio", 0.0))
    known_good_alignment_average = float(observation.get("known_good_alignment_average", 0.0))
    rust_signal_coverage = float(observation.get("rust_signal_coverage", 0.0))
    example_outcome_match_ratio = float(observation.get("example_outcome_match_ratio", 0.0))
    example_assertion_backed_ratio = float(observation.get("example_assertion_backed_ratio", 0.0))
    example_negative_evidence_strength_ratio = float(
        observation.get("example_negative_evidence_strength_ratio", 0.0)
    )

    return (
        runtime_failures,
        policy_failures,
        iso_obligation_gap_count,
        traceability_gap_count,
        target_fanout_gap_count,
        fls_gap_total,
        quality_gap_total,
        -iso_obligation_coverage,
        -fls_chapter_coverage,
        -quality_pass_ratio,
        -example_outcome_match_ratio,
        -example_assertion_backed_ratio,
        -example_negative_evidence_strength_ratio,
        -known_good_alignment_average,
        -rust_signal_coverage,
    )


def weighted_score(observation: dict[str, Any]) -> float:
    iso_coverage = float(observation.get("iso_obligation_coverage", 0.0))
    fanout_target_count = float(observation.get("decomposition_target_count", 0))
    fanout_gap_count = float(observation.get("target_fanout_gap_count", 0))
    fls_target_count = float(observation.get("fls_target_count", 0))
    fls_span_gap_count = float(observation.get("fls_span_gap_count", 0))
    fls_chapter_coverage = float(observation.get("fls_chapter_coverage", 0.0))
    quality_pass_ratio = float(observation.get("quality_pass_ratio", 0.0))
    known_good_alignment_average = float(observation.get("known_good_alignment_average", 0.0))
    rust_signal_coverage = float(observation.get("rust_signal_coverage", 0.0))
    diversity_unique_token_ratio = float(observation.get("diversity_unique_token_ratio", 0.0))
    example_outcome_match_ratio = float(observation.get("example_outcome_match_ratio", 0.0))
    example_assertion_backed_ratio = float(observation.get("example_assertion_backed_ratio", 0.0))
    example_negative_evidence_strength_ratio = float(
        observation.get("example_negative_evidence_strength_ratio", 0.0)
    )
    example_unique_signature_ratio = float(observation.get("example_unique_signature_ratio", 0.0))

    fanout_ratio = 1.0
    if fanout_target_count > 0:
        fanout_ratio = max(0.0, 1.0 - (fanout_gap_count / fanout_target_count))

    fls_span_ratio = 1.0
    if fls_target_count > 0:
        fls_span_ratio = max(0.0, 1.0 - (fls_span_gap_count / fls_target_count))
    fls_proxy = (0.7 * fls_span_ratio) + (0.3 * fls_chapter_coverage)

    runtime_failures = float(observation.get("runtime_failures", 0))
    policy_failures = float(observation.get("policy_failures", 0))
    example_gap_count = float(observation.get("example_gap_count", 0))
    example_outcome_gap_count = float(observation.get("example_outcome_gap_count", 0))
    example_assertion_gap_count = float(observation.get("example_assertion_gap_count", 0))
    example_negative_evidence_gap_count = float(
        observation.get("example_negative_evidence_gap_count", 0)
    )
    example_diversity_gap_count = float(observation.get("example_diversity_gap_count", 0))
    known_good_alignment_gap_count = float(observation.get("known_good_alignment_gap_count", 0))
    duplication_gap_count = float(observation.get("duplication_gap_count", 0))
    duplication_exception_missing_count = float(
        observation.get("duplication_exception_missing_count", 0)
    )
    rust_signal_gap_count = float(observation.get("rust_signal_gap_count", 0))
    traceability_gap_count = float(observation.get("traceability_gap_count", 0))

    penalty = (50.0 * runtime_failures) + (30.0 * policy_failures)
    penalty += (10.0 * example_gap_count) + (20.0 * traceability_gap_count)
    penalty += 12.0 * example_outcome_gap_count
    penalty += 8.0 * example_assertion_gap_count
    penalty += 8.0 * example_negative_evidence_gap_count
    penalty += 6.0 * example_diversity_gap_count
    penalty += 5.0 * known_good_alignment_gap_count
    penalty += 8.0 * duplication_gap_count
    penalty += 12.0 * duplication_exception_missing_count
    penalty += 6.0 * rust_signal_gap_count

    score = (1000.0 * iso_coverage) + (500.0 * fanout_ratio)
    score += (400.0 * fls_proxy) + (300.0 * quality_pass_ratio)
    score += 250.0 * known_good_alignment_average
    score += 150.0 * rust_signal_coverage
    score += 120.0 * diversity_unique_token_ratio
    score += 120.0 * example_outcome_match_ratio
    score += 90.0 * example_assertion_backed_ratio
    score += 80.0 * example_negative_evidence_strength_ratio
    score += 60.0 * example_unique_signature_ratio
    score -= penalty
    return round(score, 3)


def regression_flags(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    regressions: list[str] = []

    before_runtime = int(before.get("runtime_failures", 0))
    after_runtime = int(after.get("runtime_failures", 0))
    if after_runtime > before_runtime:
        regressions.append("runtime_failures_increased")

    before_policy = int(before.get("policy_failures", 0))
    after_policy = int(after.get("policy_failures", 0))
    if after_policy > before_policy:
        regressions.append("policy_failures_increased")

    lane_gap_fields = [
        "iso_obligation_gap_count",
        "traceability_gap_count",
        "target_fanout_gap_count",
        "fls_span_gap_count",
        "fls_chapter_gap_count",
        "quality_gap_count",
        "placeholder_gap_count",
        "example_gap_count",
        "example_outcome_gap_count",
        "example_assertion_gap_count",
        "example_negative_evidence_gap_count",
        "example_diversity_gap_count",
        "known_good_alignment_gap_count",
        "duplication_gap_count",
        "duplication_exception_missing_count",
        "rust_signal_gap_count",
    ]
    for field in lane_gap_fields:
        before_gap = int(before.get(field, 0))
        after_gap = int(after.get(field, 0))
        if before_gap == 0 and after_gap > 0:
            regressions.append(f"regressed_{field}")

    before_iso = float(before.get("iso_obligation_coverage", 0.0))
    after_iso = float(after.get("iso_obligation_coverage", 0.0))
    if after_iso + 1e-9 < before_iso:
        regressions.append("iso_obligation_coverage_decreased")

    before_fls = float(before.get("fls_chapter_coverage", 0.0))
    after_fls = float(after.get("fls_chapter_coverage", 0.0))
    if after_fls + 1e-9 < before_fls:
        regressions.append("fls_chapter_coverage_decreased")

    before_alignment = float(before.get("known_good_alignment_average", 0.0))
    after_alignment = float(after.get("known_good_alignment_average", 0.0))
    if after_alignment + 1e-9 < before_alignment:
        regressions.append("known_good_alignment_decreased")

    before_diversity = float(before.get("diversity_unique_token_ratio", 0.0))
    after_diversity = float(after.get("diversity_unique_token_ratio", 0.0))
    if after_diversity + 1e-9 < before_diversity:
        regressions.append("diversity_unique_token_ratio_decreased")

    before_rust = float(before.get("rust_signal_coverage", 0.0))
    after_rust = float(after.get("rust_signal_coverage", 0.0))
    if after_rust + 1e-9 < before_rust:
        regressions.append("rust_signal_coverage_decreased")

    before_example_outcome = float(before.get("example_outcome_match_ratio", 0.0))
    after_example_outcome = float(after.get("example_outcome_match_ratio", 0.0))
    if after_example_outcome + 1e-9 < before_example_outcome:
        regressions.append("example_outcome_match_ratio_decreased")

    before_example_assertion = float(before.get("example_assertion_backed_ratio", 0.0))
    after_example_assertion = float(after.get("example_assertion_backed_ratio", 0.0))
    if after_example_assertion + 1e-9 < before_example_assertion:
        regressions.append("example_assertion_backed_ratio_decreased")

    before_negative_strength = float(before.get("example_negative_evidence_strength_ratio", 0.0))
    after_negative_strength = float(after.get("example_negative_evidence_strength_ratio", 0.0))
    if after_negative_strength + 1e-9 < before_negative_strength:
        regressions.append("example_negative_evidence_strength_ratio_decreased")

    return regressions


def improves(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return metric_vector(after) < metric_vector(before)


def critical_high_deficit_count(observation: dict[str, Any]) -> int:
    deficits = observation.get("deficits") or []
    return sum(
        1
        for deficit in deficits
        if str(deficit.get("severity") or "").strip() in {"critical", "high"}
    )


def critical_high_reduction(before: dict[str, Any], after: dict[str, Any]) -> int:
    return critical_high_deficit_count(before) - critical_high_deficit_count(after)


def evaluation_sort_key(before: dict[str, Any], evaluation: dict[str, Any]) -> tuple[Any, ...]:
    after = evaluation.get("observation") or {}
    vector = tuple(evaluation.get("metric_vector") or metric_vector(after))
    reduction = critical_high_reduction(before, after)
    weighted = float(evaluation.get("weighted_score") or weighted_score(after))
    footprint = int(evaluation.get("mutation_footprint_estimate") or 0)
    candidate_id = str(evaluation.get("candidate_id") or "")
    return (
        vector,
        -reduction,
        -weighted,
        footprint,
        candidate_id,
    )
