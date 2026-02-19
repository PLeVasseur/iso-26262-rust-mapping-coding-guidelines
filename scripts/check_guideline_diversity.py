#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import re
import sys
from pathlib import Path
from typing import Any

from _common import EXIT_POLICY_FAIL, EXIT_SUCCESS, load_guidelines_payload, read_yaml, repo_root, write_json
from known_good_lib import cosine_similarity, tokenize_words, utc_now

CONDITION_TERMS = {
    "if",
    "when",
    "unless",
    "before",
    "after",
    "must",
    "shall",
    "require",
    "forbid",
    "only",
    "never",
    "always",
    "at",
    "least",
    "more",
    "than",
    "under",
    "over",
    "equal",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check guideline corpus for duplication/diversity")
    parser.add_argument("--todo-guidelines", type=Path, default=Path("data/todo_guidelines.yaml"))
    parser.add_argument("--policy", type=Path, default=Path("config/diversity_policy.yaml"))
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def normalize_text(text: str) -> str:
    lowered = text.lower().strip()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def lead_in(text: str, token_window: int) -> str:
    tokens = tokenize_words(text)
    if not tokens:
        return ""
    return " ".join(tokens[:token_window])


def evidence_artifact_classes(guideline: dict[str, Any]) -> set[str]:
    classes: set[str] = set()
    for entry in guideline.get("evidence_artifacts", []) or []:
        value = str(entry).strip()
        if not value:
            continue
        first_segment = value.split("/", maxsplit=1)[0]
        if first_segment:
            classes.add(first_segment)
    return classes


def non_compliant_expectation(guideline: dict[str, Any]) -> str:
    examples = guideline.get("examples") or {}
    non_compliant = examples.get("non_compliant") or {}
    return str(non_compliant.get("compile_expectation") or "").strip()


def bounded_condition_terms(guideline: dict[str, Any]) -> set[str]:
    text = "\n".join(
        [
            str(guideline.get("rule_statement") or ""),
            str(guideline.get("amplification") or ""),
            str(guideline.get("exceptions") or ""),
            str(guideline.get("rationale") or ""),
        ]
    ).lower()
    tokens = set(tokenize_words(text))
    selected = {token for token in tokens if token in CONDITION_TERMS}
    for match in re.findall(r"\b\d+\b", text):
        selected.add(match)
    for operator in ["<=", ">=", "<", ">", "=="]:
        if operator in text:
            selected.add(operator)
    return selected


def verification_delta_count(a: dict[str, Any], b: dict[str, Any]) -> int:
    delta = 0

    if str(a.get("decidable_status") or "") != str(b.get("decidable_status") or ""):
        delta += 1
    if non_compliant_expectation(a) != non_compliant_expectation(b):
        delta += 1
    if str(a.get("enforcement_mode") or "") != str(b.get("enforcement_mode") or ""):
        delta += 1
    if evidence_artifact_classes(a) != evidence_artifact_classes(b):
        delta += 1
    if bounded_condition_terms(a) != bounded_condition_terms(b):
        delta += 1

    return delta


def obligations(guideline: dict[str, Any]) -> set[str]:
    return {
        str(item).strip()
        for item in (guideline.get("obligation_units") or [])
        if str(item).strip()
    }


def evaluate_near_duplicate_pair(
    guideline_a: dict[str, Any],
    guideline_b: dict[str, Any],
    similarity: float,
    policy: dict[str, Any],
) -> dict[str, Any]:
    near_policy = policy.get("near_duplicate") or {}
    allow_distinct_obligation = bool(
        near_policy.get("allow_near_duplicate_with_distinct_obligation_unit", True)
    )
    require_verification_delta = bool(
        near_policy.get("require_verification_constraint_divergence", True)
    )
    min_verification_delta = int(near_policy.get("min_verification_constraint_delta_count") or 1)
    disallow_same_family = bool(
        near_policy.get("disallow_near_duplicate_within_same_rule_family", True)
    )

    family_a = str(guideline_a.get("rule_family_id") or "").strip()
    family_b = str(guideline_b.get("rule_family_id") or "").strip()
    same_rule_family = bool(family_a and family_a == family_b)

    obligations_a = obligations(guideline_a)
    obligations_b = obligations(guideline_b)
    different_obligation_unit = bool(
        obligations_a
        and obligations_b
        and obligations_a != obligations_b
        and obligations_a.isdisjoint(obligations_b)
    )
    verification_delta = verification_delta_count(guideline_a, guideline_b)

    reason_codes: list[str] = []
    allowed_exception = False
    violates_policy = True

    if disallow_same_family and same_rule_family:
        reason_codes.append("same_rule_family_disallowed")

    if allow_distinct_obligation and different_obligation_unit:
        reason_codes.append("distinct_obligation_unit")
    else:
        reason_codes.append("obligation_unit_not_distinct")

    if require_verification_delta:
        if verification_delta >= min_verification_delta:
            reason_codes.append("verification_constraints_diverged")
        else:
            reason_codes.append("verification_constraints_not_diverged")
    else:
        reason_codes.append("verification_divergence_not_required")

    if allow_distinct_obligation and different_obligation_unit:
        if not require_verification_delta or verification_delta >= min_verification_delta:
            if not (disallow_same_family and same_rule_family):
                allowed_exception = True
                violates_policy = False
                reason_codes.append("allowed_exception")

    return {
        "similarity": round(float(similarity), 6),
        "same_rule_family": same_rule_family,
        "different_obligation_unit": different_obligation_unit,
        "verification_delta_count": verification_delta,
        "allowed_exception": allowed_exception,
        "violates_policy": violates_policy,
        "reason_codes": reason_codes,
    }


def main() -> int:
    args = parse_args()
    root = repo_root()

    policy = read_yaml(root / args.policy) or {}
    gate_mode = str(policy.get("gate_mode") or "warn").strip().lower()
    if gate_mode not in {"warn", "error"}:
        gate_mode = "warn"
    hard_fail = gate_mode == "error"

    exact_policy = policy.get("exact_duplicate") or {}
    near_policy = policy.get("near_duplicate") or {}
    lead_policy = policy.get("lead_in") or {}
    lexical_policy = policy.get("lexical_diversity") or {}

    max_exact_groups = int(exact_policy.get("max_groups") or 0)
    similarity_threshold = float(near_policy.get("similarity_threshold") or 0.9)
    max_near_violations = int(near_policy.get("max_violation_pairs") or 0)

    token_window = int(lead_policy.get("token_window") or 8)
    max_repeated_lead = int(lead_policy.get("max_repeated_lead_in_count") or 2)
    min_unique_token_ratio = float(lexical_policy.get("min_unique_token_ratio") or 0.0)

    payload = load_guidelines_payload(root / args.todo_guidelines)
    guidelines = payload.get("guidelines") or []

    indexed = []
    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if not guideline_id:
            continue
        rule_statement = str(guideline.get("rule_statement") or "")
        amplification = str(guideline.get("amplification") or "")
        exceptions = str(guideline.get("exceptions") or "")
        rationale = str(guideline.get("rationale") or "")
        combined = "\n".join([rule_statement, amplification, exceptions, rationale])
        indexed.append(
            {
                "guideline": guideline,
                "guideline_id": guideline_id,
                "rule_statement": rule_statement,
                "combined_text": combined,
                "normalized_rule": normalize_text(rule_statement),
                "lead_in": lead_in(rule_statement, token_window),
                "rule_family_id": str(guideline.get("rule_family_id") or "").strip(),
                "obligations": obligations(guideline),
            }
        )

    # Exact duplicates.
    exact_groups_raw: dict[str, list[str]] = {}
    for item in indexed:
        key = str(item["normalized_rule"])
        if not key:
            continue
        exact_groups_raw.setdefault(key, []).append(str(item["guideline_id"]))

    exact_duplicate_groups = []
    for key, ids in sorted(exact_groups_raw.items()):
        unique_ids = sorted(set(ids))
        if len(unique_ids) <= 1:
            continue
        exact_duplicate_groups.append(
            {
                "normalized_rule_statement": key,
                "guideline_ids": unique_ids,
            }
        )

    # Lead-in clusters.
    lead_raw: dict[str, list[str]] = {}
    for item in indexed:
        key = str(item["lead_in"])
        if not key:
            continue
        lead_raw.setdefault(key, []).append(str(item["guideline_id"]))

    lead_in_clusters = []
    for key, ids in sorted(lead_raw.items()):
        unique_ids = sorted(set(ids))
        if len(unique_ids) <= 1:
            continue
        lead_in_clusters.append(
            {
                "lead_in": key,
                "count": len(unique_ids),
                "guideline_ids": unique_ids,
                "violates_policy": len(unique_ids) > max_repeated_lead,
            }
        )

    # Near duplicates.
    near_duplicate_pairs = []
    near_duplicate_violations = 0
    near_duplicate_allowed = 0

    for a, b in itertools.combinations(indexed, 2):
        similarity = cosine_similarity(str(a["combined_text"]), str(b["combined_text"]))
        if similarity < similarity_threshold:
            continue

        guideline_a = a["guideline"]
        guideline_b = b["guideline"]
        evaluation = evaluate_near_duplicate_pair(guideline_a, guideline_b, similarity, policy)

        if bool(evaluation["violates_policy"]):
            near_duplicate_violations += 1
        else:
            near_duplicate_allowed += 1

        near_duplicate_pairs.append(
            {
                "guideline_a": str(a["guideline_id"]),
                "guideline_b": str(b["guideline_id"]),
                **evaluation,
            }
        )

    # Lexical diversity.
    token_pool = []
    for item in indexed:
        token_pool.extend(tokenize_words(str(item["rule_statement"])))
    unique_token_ratio = 0.0
    if token_pool:
        unique_token_ratio = len(set(token_pool)) / len(token_pool)

    lead_in_violations = len([item for item in lead_in_clusters if item.get("violates_policy")])
    exact_group_count = len(exact_duplicate_groups)

    violation_messages: list[str] = []
    if exact_group_count > max_exact_groups:
        violation_messages.append(
            f"exact duplicate groups {exact_group_count} > allowed {max_exact_groups}"
        )
    if lead_in_violations > 0:
        violation_messages.append(
            f"repeated lead-in clusters violating policy: {lead_in_violations}"
        )
    if near_duplicate_violations > max_near_violations:
        violation_messages.append(
            "near-duplicate policy violations "
            f"{near_duplicate_violations} > allowed {max_near_violations}"
        )
    if unique_token_ratio < min_unique_token_ratio:
        violation_messages.append(
            f"unique token ratio {unique_token_ratio:.3f} < minimum {min_unique_token_ratio:.3f}"
        )

    warnings: list[str] = []
    errors: list[str] = []
    for message in violation_messages:
        if hard_fail:
            errors.append(message)
        else:
            warnings.append(message)

    report = {
        "version": 1,
        "generated_at": utc_now(),
        "policy_mode": "error" if hard_fail else "warn",
        "guideline_count": len(indexed),
        "metrics": {
            "unique_token_ratio": round(unique_token_ratio, 6),
            "exact_duplicate_group_count": exact_group_count,
            "lead_in_violation_count": lead_in_violations,
            "near_duplicate_pair_count": len(near_duplicate_pairs),
            "near_duplicate_violation_count": near_duplicate_violations,
            "near_duplicate_exception_allowed_count": near_duplicate_allowed,
        },
        "exact_duplicate_groups": exact_duplicate_groups,
        "lead_in_clusters": lead_in_clusters,
        "near_duplicate_pairs": near_duplicate_pairs,
        "warning_count": len(warnings),
        "error_count": len(errors),
        "warnings": warnings,
        "errors": errors,
        "ok": len(errors) == 0,
    }

    if args.json_output:
        write_json(args.json_output, report)

    if errors:
        print(f"[guideline-diversity] failed with {len(errors)} error(s)")
        for message in errors:
            print(f"[guideline-diversity][error] {message}")
        return EXIT_POLICY_FAIL

    print(
        "[guideline-diversity] "
        f"ok guidelines={len(indexed)} near_pairs={len(near_duplicate_pairs)} "
        f"warnings={len(warnings)}"
    )
    for message in warnings:
        print(f"[guideline-diversity][warn] {message}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
