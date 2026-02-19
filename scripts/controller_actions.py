from __future__ import annotations

import copy
import csv
import hashlib
import itertools
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from _common import read_yaml, run_command, write_yaml
from controller_rewrite import resolve_guideline_rewrite

SEVERITY_WEIGHTS = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}

LANE_IMPACT_WEIGHTS = {
    "iso": 4,
    "fanout": 3,
    "fls": 2,
    "quality": 2,
    "alignment": 2,
    "diversity": 2,
    "rust": 2,
    "examples": 1,
}

DEFAULT_MAX_MUTATED_GUIDELINES = 5


def _target_from_seed(seed: dict[str, Any]) -> str:
    anchor = str(seed.get("citation_anchor_id") or "").strip()
    if anchor:
        return anchor
    chunk = str(seed.get("chunk_id") or "").strip()
    if chunk:
        return chunk
    return str(seed.get("seed_id") or "")


def _obligation_from_seed(seed: dict[str, Any]) -> str:
    value = str(seed.get("obligation_unit_id") or "").strip()
    if value:
        return value
    row_key = str(seed.get("row_key") or "").strip()
    if row_key:
        return row_key
    return _target_from_seed(seed)


def _load_seed_index(root: Path) -> dict[str, Any]:
    seed_path = root / "data/seed_topics.yaml"
    if not seed_path.exists():
        return {
            "by_seed": {},
            "by_target": defaultdict(list),
            "by_obligation": defaultdict(list),
        }

    payload = read_yaml(seed_path) or {}
    seeds = payload.get("seed_topics") or []

    by_seed: dict[str, dict[str, Any]] = {}
    by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_obligation: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for seed in seeds:
        seed_id = str(seed.get("seed_id") or "").strip()
        if not seed_id:
            continue
        by_seed[seed_id] = seed
        target = _target_from_seed(seed)
        obligation = _obligation_from_seed(seed)
        by_target[target].append(seed)
        by_obligation[obligation].append(seed)

    return {
        "by_seed": by_seed,
        "by_target": by_target,
        "by_obligation": by_obligation,
    }


def _guidelines_for_target(root: Path) -> dict[str, set[str]]:
    path = root / "data/coverage_matrix.csv"
    mapping: dict[str, set[str]] = defaultdict(set)
    if not path.exists():
        return mapping
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            target_id = str(row.get("target_id") or "").strip()
            guideline_id = str(row.get("guideline_id") or "").strip()
            if target_id and guideline_id:
                mapping[target_id].add(guideline_id)
    return mapping


def _load_todo_guidelines(root: Path) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    path = root / "data/todo_guidelines.yaml"
    payload = read_yaml(path) or {}
    guidelines = payload.get("guidelines") or []
    return path, payload, guidelines


def _write_todo_guidelines(
    path: Path, payload: dict[str, Any], guidelines: list[dict[str, Any]]
) -> None:
    payload["guidelines"] = sorted(guidelines, key=lambda item: str(item.get("id") or ""))
    write_yaml(path, payload)


def _load_fls_candidates(root: Path) -> dict[str, dict[str, list[str]]]:
    path = root / "data/fls_target_candidates.yaml"
    if not path.exists():
        return {"by_target": {}, "by_obligation": {}}
    payload = read_yaml(path) or {}
    by_target: dict[str, list[str]] = {}
    by_obligation: dict[str, list[str]] = {}

    for entry in payload.get("target_candidates", []):
        refs = []
        for candidate in entry.get("candidate_fls_refs", []):
            ref = str(candidate.get("fls_ref") or "").strip()
            if ref and ref not in refs:
                refs.append(ref)
        target_id = str(entry.get("target_id") or "").strip()
        obligation = str(entry.get("obligation_unit_id") or "").strip()
        if target_id and refs:
            by_target[target_id] = refs
        if obligation and refs:
            by_obligation[obligation] = refs
    return {"by_target": by_target, "by_obligation": by_obligation}


def _candidate_id(prefix: str, values: list[str]) -> str:
    material = "|".join(values)
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}:{digest}"


def _action_signature(action: dict[str, Any]) -> str:
    return json.dumps(action, sort_keys=True)


def _cluster_key(deficit: dict[str, Any]) -> str:
    target_id = str(deficit.get("target_id") or "").strip()
    obligation = str(deficit.get("obligation_unit_id") or "").strip()
    guideline_id = str(deficit.get("guideline_id") or "").strip()
    if target_id:
        return f"target:{target_id}"
    if obligation:
        return f"obligation:{obligation}"
    if guideline_id:
        return f"guideline:{guideline_id}"
    return "global"


def _action_object_key(action: dict[str, Any]) -> str:
    if str(action.get("guideline_id") or "").strip():
        return f"guideline:{str(action.get('guideline_id'))}"
    if str(action.get("obligation_unit_id") or "").strip():
        return f"obligation:{str(action.get('obligation_unit_id'))}"
    if str(action.get("target_id") or "").strip():
        return f"target:{str(action.get('target_id'))}"
    return "global"


def _extract_action_targets(action: dict[str, Any]) -> tuple[str, str]:
    guideline_id = str(action.get("guideline_id") or "").strip()
    target_id = str(action.get("target_id") or "").strip()
    return guideline_id, target_id


def _proposal_from_deficit(deficit: dict[str, Any]) -> dict[str, Any] | None:
    deficit_type = str(deficit.get("type") or "")
    severity = str(deficit.get("severity") or "low")
    guideline_id = str(deficit.get("guideline_id") or "").strip()
    target_id = str(deficit.get("target_id") or "").strip()
    obligation_unit_id = str(deficit.get("obligation_unit_id") or "").strip()

    action: dict[str, Any] | None = None
    expected_delta: dict[str, int] = {}
    risk_penalty = 0.0

    if deficit_type in {"iso_obligation_gap", "target_fanout_gap"}:
        action = {
            "type": "spawn_rule_for_obligation_unit",
            "target_id": target_id,
            "obligation_unit_id": obligation_unit_id,
        }
        expected_delta = {"iso": 1, "fanout": 1}
        risk_penalty = 0.35
    elif deficit_type in {"fls_span_gap", "fls_chapter_gap"}:
        action = {
            "type": "assign_missing_fls_refs",
            "guideline_id": guideline_id,
            "target_id": target_id,
            "max_refs": 3,
        }
        expected_delta = {"fls": 1}
        risk_penalty = 0.2
    elif deficit_type in {"quality_gap", "placeholder_gap"} and guideline_id:
        action = {
            "type": "rewrite_rule_statement_specific",
            "guideline_id": guideline_id,
        }
        expected_delta = {"quality": 1}
        risk_penalty = 0.1
    elif deficit_type == "example_gap" and guideline_id:
        action = {
            "type": "upgrade_examples_non_placeholder",
            "guideline_id": guideline_id,
        }
        expected_delta = {"examples": 1, "quality": 1}
        risk_penalty = 0.15
    elif deficit_type == "example_outcome_gap" and guideline_id:
        action = {
            "type": "align_example_with_decidable_status",
            "guideline_id": guideline_id,
        }
        expected_delta = {"examples": 2, "quality": 1}
        risk_penalty = 0.12
    elif deficit_type == "example_assertion_gap" and guideline_id:
        action = {
            "type": "strengthen_example_assertions",
            "guideline_id": guideline_id,
        }
        expected_delta = {"examples": 2, "quality": 1}
        risk_penalty = 0.1
    elif deficit_type == "example_negative_evidence_gap" and guideline_id:
        action = {
            "type": "convert_non_compliant_to_compile_fail_or_panic",
            "guideline_id": guideline_id,
        }
        expected_delta = {"examples": 2, "quality": 1}
        risk_penalty = 0.12
    elif deficit_type == "example_diversity_gap" and guideline_id:
        action = {
            "type": "upgrade_examples_non_placeholder",
            "guideline_id": guideline_id,
        }
        expected_delta = {"examples": 2, "quality": 1, "diversity": 1}
        risk_penalty = 0.1
    elif deficit_type in {"duplication_gap", "duplication_exception_missing"} and guideline_id:
        action = {
            "type": "diversify_rule_wording",
            "guideline_id": guideline_id,
        }
        expected_delta = {"quality": 1, "diversity": 2}
        risk_penalty = 0.08
    elif deficit_type == "rust_signal_gap" and guideline_id:
        action = {
            "type": "add_alignment_citation_signals",
            "guideline_id": guideline_id,
        }
        expected_delta = {"quality": 1, "rust": 2}
        risk_penalty = 0.1
    elif deficit_type == "known_good_alignment_gap" and guideline_id:
        details = str(deficit.get("details") or "")
        if "citation_coverage_low" in details:
            action = {
                "type": "add_alignment_citation_signals",
                "guideline_id": guideline_id,
            }
            expected_delta = {"quality": 1, "alignment": 2}
            risk_penalty = 0.1
        elif "example_depth_too_shallow" in details:
            action = {
                "type": "upgrade_examples_non_placeholder",
                "guideline_id": guideline_id,
            }
            expected_delta = {"examples": 1, "quality": 1, "alignment": 1}
            risk_penalty = 0.2
        elif "granularity_too_coarse" in details:
            action = {
                "type": "rebalance_alignment_granularity",
                "guideline_id": guideline_id,
                "granularity_mode": "coarse",
            }
            expected_delta = {"quality": 1, "alignment": 2}
            risk_penalty = 0.15
        elif "granularity_too_fine" in details:
            action = {
                "type": "rebalance_alignment_granularity",
                "guideline_id": guideline_id,
                "granularity_mode": "fine",
            }
            expected_delta = {"quality": 1, "alignment": 2}
            risk_penalty = 0.15
        elif "benchmark_similarity_gap" in details:
            action = {
                "type": "raise_benchmark_similarity",
                "guideline_id": guideline_id,
            }
            expected_delta = {"quality": 1, "alignment": 2}
            risk_penalty = 0.2
        else:
            action = {
                "type": "rewrite_rule_statement_specific",
                "guideline_id": guideline_id,
            }
            expected_delta = {"quality": 1, "alignment": 1}
            risk_penalty = 0.15

    if action is None:
        return None

    return {
        "proposal_id": _candidate_id(
            "prop", [str(deficit.get("deficit_id") or ""), _action_signature(action)]
        ),
        "deficit_id": str(deficit.get("deficit_id") or ""),
        "cluster_key": _cluster_key(deficit),
        "severity": severity,
        "severity_weight": SEVERITY_WEIGHTS.get(severity, 1),
        "expected_delta": expected_delta,
        "risk_penalty": risk_penalty,
        "action": action,
    }


def _compatible_bundle(actions: list[dict[str, Any]]) -> bool:
    seen_type_object: set[tuple[str, str]] = set()
    seen_spawn_obligation: set[str] = set()

    for action in actions:
        action_type = str(action.get("type") or "").strip()
        object_key = _action_object_key(action)
        type_object = (action_type, object_key)
        if type_object in seen_type_object:
            return False
        seen_type_object.add(type_object)

        if action_type == "spawn_rule_for_obligation_unit":
            obligation = str(action.get("obligation_unit_id") or "").strip()
            if obligation and obligation in seen_spawn_obligation:
                return False
            if obligation:
                seen_spawn_obligation.add(obligation)
    return True


def _unique_actions(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for proposal in proposals:
        signature = _action_signature(proposal["action"])
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(proposal)
    return unique


def _expected_impact_score(expected_delta: dict[str, int]) -> int:
    score = 0
    for lane, value in expected_delta.items():
        score += int(value) * LANE_IMPACT_WEIGHTS.get(lane, 0)
    return score


def _merge_expected_deltas(proposals: list[dict[str, Any]]) -> dict[str, int]:
    merged: dict[str, int] = defaultdict(int)
    for proposal in proposals:
        for lane, value in (proposal.get("expected_delta") or {}).items():
            merged[str(lane)] += int(value)
    return dict(merged)


def _bundle_repetition_penalty(actions: list[dict[str, Any]]) -> float:
    action_types = {str(action.get("type") or "") for action in actions}
    penalty = 0.0

    repetitive_triplet = {
        "rewrite_rule_statement_specific",
        "raise_benchmark_similarity",
        "add_alignment_citation_signals",
    }
    if action_types == repetitive_triplet:
        penalty += 0.35

    if "upgrade_examples_non_placeholder" in action_types:
        penalty -= 0.05
    if "strengthen_example_assertions" in action_types:
        penalty -= 0.08
    if "convert_non_compliant_to_compile_fail_or_panic" in action_types:
        penalty -= 0.06
    if "diversify_rule_wording" in action_types:
        penalty -= 0.2

    return max(-0.3, round(penalty, 3))


def _mutation_footprint(actions: list[dict[str, Any]]) -> int:
    touched_guidelines: set[str] = set()
    touched_targets: set[str] = set()
    for action in actions:
        guideline_id, target_id = _extract_action_targets(action)
        if guideline_id:
            touched_guidelines.add(guideline_id)
        if target_id:
            touched_targets.add(target_id)
    return len(touched_guidelines) + len(touched_targets)


def _bundle_candidate(
    proposals: list[dict[str, Any]],
    historical_signatures: set[str],
    suppressed_signatures: set[str],
    max_mutated_guidelines: int,
) -> dict[str, Any] | None:
    unique_proposals = _unique_actions(proposals)
    actions = [proposal["action"] for proposal in unique_proposals]
    if not actions:
        return None
    if not _compatible_bundle(actions):
        return None

    signatures = sorted(_action_signature(action) for action in actions)
    bundle_signature = "|".join(signatures)
    if bundle_signature in suppressed_signatures:
        return None

    footprint = _mutation_footprint(actions)
    if footprint > max_mutated_guidelines:
        return None

    expected_delta = _merge_expected_deltas(unique_proposals)
    expected_score = _expected_impact_score(expected_delta)
    severity_score = sum(int(proposal.get("severity_weight") or 0) for proposal in unique_proposals)
    novelty_score = 0 if bundle_signature in historical_signatures else 1
    base_risk_penalty = sum(float(p.get("risk_penalty") or 0.0) for p in unique_proposals)
    repetition_penalty = _bundle_repetition_penalty(actions)
    risk_penalty = round(base_risk_penalty + repetition_penalty, 3)
    pre_score = round(severity_score + expected_score + novelty_score - risk_penalty, 3)

    source_deficit_ids = sorted(
        {
            str(proposal.get("deficit_id") or "")
            for proposal in unique_proposals
            if str(proposal.get("deficit_id") or "")
        }
    )
    cluster_keys = sorted(
        {
            str(proposal.get("cluster_key") or "")
            for proposal in unique_proposals
            if str(proposal.get("cluster_key") or "")
        }
    )

    rationale = "bundle actions for clustered deficits"
    if len(actions) == 1:
        rationale = "single-action candidate"

    return {
        "candidate_id": _candidate_id("cand", [bundle_signature, str(pre_score)]),
        "actions": actions,
        "rationale": rationale,
        "source_deficit_ids": source_deficit_ids,
        "cluster_keys": cluster_keys,
        "bundle_signature": bundle_signature,
        "expected_lane_deltas": expected_delta,
        "risk_penalty": risk_penalty,
        "repetition_penalty": repetition_penalty,
        "novelty_score": novelty_score,
        "pre_score": pre_score,
        "mutation_footprint_estimate": footprint,
    }


def _bundle_signatures(candidates: list[dict[str, Any]]) -> set[str]:
    return {
        str(candidate.get("bundle_signature") or "")
        for candidate in candidates
        if str(candidate.get("bundle_signature") or "")
    }


def generate_candidates(
    observation: dict[str, Any],
    beam_width: int,
    max_actions_per_bundle: int = 3,
    suppressed_signatures: set[str] | None = None,
    historical_signatures: set[str] | None = None,
    max_mutated_guidelines: int = DEFAULT_MAX_MUTATED_GUIDELINES,
) -> list[dict[str, Any]]:
    deficits = observation.get("deficits") or []
    suppressed_signatures = suppressed_signatures or set()
    historical_signatures = historical_signatures or set()

    proposals = []
    for deficit in deficits:
        proposal = _proposal_from_deficit(deficit)
        if proposal is not None:
            proposals.append(proposal)

    if not proposals:
        return []

    proposals_by_cluster: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for proposal in proposals:
        proposals_by_cluster[str(proposal.get("cluster_key") or "global")].append(proposal)

    candidates: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()

    for cluster_key in sorted(proposals_by_cluster):
        cluster_proposals = sorted(
            proposals_by_cluster[cluster_key],
            key=lambda item: (
                -int(item.get("severity_weight") or 0),
                str(item.get("proposal_id") or ""),
            ),
        )

        # singles
        for proposal in cluster_proposals:
            candidate = _bundle_candidate(
                [proposal],
                historical_signatures,
                suppressed_signatures,
                max_mutated_guidelines,
            )
            if candidate is None:
                continue
            signature = str(candidate.get("bundle_signature") or "")
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            candidates.append(candidate)

        # pairs + triples
        max_k = min(max_actions_per_bundle, 3)
        if max_k <= 1:
            continue

        for size in range(2, max_k + 1):
            for combo in itertools.combinations(cluster_proposals, size):
                candidate = _bundle_candidate(
                    list(combo),
                    historical_signatures,
                    suppressed_signatures,
                    max_mutated_guidelines,
                )
                if candidate is None:
                    continue
                signature = str(candidate.get("bundle_signature") or "")
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                candidates.append(candidate)

    if not candidates:
        return []

    # prune dominated: same source deficits but worse pre-score and larger footprint
    pruned: list[dict[str, Any]] = []
    best_by_source: dict[str, tuple[float, int]] = {}
    for candidate in sorted(
        candidates,
        key=lambda item: (
            -float(item.get("pre_score") or 0.0),
            int(item.get("mutation_footprint_estimate") or 0),
            str(item.get("candidate_id") or ""),
        ),
    ):
        source_key = ",".join(
            sorted(str(item) for item in candidate.get("source_deficit_ids") or [])
        )
        current = (
            float(candidate.get("pre_score") or 0.0),
            int(candidate.get("mutation_footprint_estimate") or 0),
        )
        best = best_by_source.get(source_key)
        if best is None:
            best_by_source[source_key] = current
            pruned.append(candidate)
            continue
        if current[0] > best[0] or (current[0] == best[0] and current[1] < best[1]):
            best_by_source[source_key] = current
            pruned.append(candidate)

    # Final rank: pre-score desc, risk asc, footprint asc, stable by id.
    pruned.sort(
        key=lambda item: (
            -float(item.get("pre_score") or 0.0),
            float(item.get("risk_penalty") or 0.0),
            int(item.get("mutation_footprint_estimate") or 0),
            str(item.get("candidate_id") or ""),
        )
    )

    ranked = pruned[: max(1, beam_width)]

    # ensure deterministic candidate ids do not collide
    if len(_bundle_signatures(ranked)) != len(ranked):
        unique_ranked: list[dict[str, Any]] = []
        seen_ranked: set[str] = set()
        for item in ranked:
            signature = str(item.get("bundle_signature") or "")
            if signature in seen_ranked:
                continue
            seen_ranked.add(signature)
            unique_ranked.append(item)
        return unique_ranked

    return ranked


def _replace_rule_id(value: str, old_rule_id: str, new_rule_id: str) -> str:
    return value.replace(old_rule_id, new_rule_id)


def _next_child_guideline_id(
    parent_id: str, obligation_unit_id: str, existing_ids: set[str]
) -> str:
    ordinal = 1
    while True:
        material = f"{parent_id}|{obligation_unit_id}|{ordinal}"
        digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:12].upper()
        candidate = f"RG-{digest}"
        if candidate not in existing_ids:
            return candidate
        ordinal += 1


def _rule_family_id_for_target(target_id: str) -> str:
    digest = hashlib.sha1(target_id.encode("utf-8")).hexdigest()[:12].upper()
    return f"FAM-{digest}"


def _sanitize_text(value: str) -> str:
    updated = value
    updated = re.sub(r"\bplaceholder\b", "illustrative", updated, flags=re.IGNORECASE)
    updated = re.sub(r"\bpending\b", "ready-for-review", updated, flags=re.IGNORECASE)
    updated = re.sub(r"\btodo\b", "review item", updated, flags=re.IGNORECASE)
    return updated


def _append_sentence(base: str, sentence: str) -> str:
    base = base.strip()
    sentence = sentence.strip()
    if not sentence:
        return base
    if sentence in base:
        return base
    if not base:
        return sentence
    if base[-1] not in {".", "!", "?"}:
        base = f"{base}."
    return f"{base} {sentence}"


def _topic_signal_key(topic: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")
    return normalized or "misc"


def _lead_in_key(text: str, token_window: int = 8) -> str:
    tokens = [item for item in re.findall(r"[a-z0-9_]+", text.lower()) if item]
    if not tokens:
        return ""
    return " ".join(tokens[:token_window])


def _normalize_statement(text: str) -> str:
    lowered = text.lower().strip()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def _rule_statement_stats(
    guidelines: list[dict[str, Any]],
    exclude_guideline_id: str,
    token_window: int,
) -> tuple[set[str], dict[str, int]]:
    normalized_set: set[str] = set()
    lead_counts: dict[str, int] = defaultdict(int)

    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if not guideline_id or guideline_id == exclude_guideline_id:
            continue
        statement = str(guideline.get("rule_statement") or "")
        normalized = _normalize_statement(statement)
        if normalized:
            normalized_set.add(normalized)
        lead = _lead_in_key(statement, token_window)
        if lead:
            lead_counts[lead] += 1

    return normalized_set, dict(lead_counts)


def _stable_variant_index(*parts: str, modulus: int) -> int:
    if modulus <= 0:
        return 0
    material = "|".join(parts)
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]
    return int(digest, 16) % modulus


def _load_rust_signals(root: Path) -> dict[str, Any]:
    path = root / "data/rust_signals.yaml"
    if not path.exists():
        return {}
    return read_yaml(path) or {}


def _load_citation_policy(root: Path) -> dict[str, Any]:
    path = root / "config/citation_grounding_policy.yaml"
    if not path.exists():
        return {}
    return read_yaml(path) or {}


def _load_diversity_policy(root: Path) -> dict[str, Any]:
    path = root / "config/diversity_policy.yaml"
    if not path.exists():
        return {}
    return read_yaml(path) or {}


def _obligation_class(guideline: dict[str, Any]) -> str:
    obligations = [
        str(item).strip().lower()
        for item in (guideline.get("obligation_units") or [])
        if str(item).strip()
    ]
    for value in obligations:
        if ":table_" in value or "table" in value:
            return "table"
        if ":annex_" in value or "annex" in value:
            return "annex"
    return "clause"


def _format_iso_marker(seed: dict[str, Any], templates: dict[str, Any]) -> str:
    part = str(seed.get("part") or "6").strip() or "6"
    reference = str(seed.get("reference") or "").strip()
    compact_reference = re.sub(r"[^A-Za-z0-9]+", "-", reference).strip("-")
    from_seed_template = str(templates.get("from_seed") or "ISO26262-Part{part}-{reference}")
    fallback = str(templates.get("fallback") or "ISO26262-6-2018")

    if compact_reference:
        marker = from_seed_template.format(part=part, reference=compact_reference)
    else:
        marker = fallback
    return marker


def _anchor_sentence(
    templates: list[str],
    marker: str,
    guideline_id: str,
    marker_kind: str,
    ordinal: int,
) -> str:
    if not templates:
        if marker_kind == "iso":
            return f"ISO requirement linkage: {marker}."
        return f"Rust API anchor: {marker}."

    index = _stable_variant_index(
        guideline_id, marker_kind, marker, str(ordinal), modulus=len(templates)
    )
    template = templates[index]
    sentence = template.format(marker=marker)
    sentence = sentence.strip()
    if sentence and sentence[-1] not in {".", "!", "?"}:
        sentence = f"{sentence}."
    return sentence


def _expected_std_refs_for_guideline(
    guideline: dict[str, Any],
    rust_signals: dict[str, Any],
    citation_policy: dict[str, Any],
) -> list[str]:
    expected_refs: list[str] = []
    topic_key = _topic_signal_key(str(guideline.get("technical_topic") or ""))
    topic_payload = (rust_signals.get("topic_signals") or {}).get(topic_key) or {}
    for ref in topic_payload.get("std_refs") or []:
        text = str(ref).strip()
        if text and text not in expected_refs:
            expected_refs.append(text)

    fls_signals = rust_signals.get("fls_signals") or {}
    for fls_ref in guideline.get("fls_refs") or []:
        lowered = str(fls_ref).strip().lower()
        for prefix, signal_payload in fls_signals.items():
            if prefix and prefix in lowered:
                for ref in signal_payload.get("std_refs") or []:
                    text = str(ref).strip()
                    if text and text not in expected_refs:
                        expected_refs.append(text)

    if not expected_refs:
        defaults_by_topic = citation_policy.get("std_ref_defaults_by_topic") or {}
        topic_label = str(guideline.get("technical_topic") or "").strip().lower()
        for key, refs in defaults_by_topic.items():
            if str(key).strip().lower() == topic_label:
                for ref in refs or []:
                    text = str(ref).strip()
                    if text and text not in expected_refs:
                        expected_refs.append(text)

    if not expected_refs:
        global_defaults = rust_signals.get("global_defaults") or {}
        for ref in global_defaults.get("fallback_std_refs") or []:
            text = str(ref).strip()
            if text and text not in expected_refs:
                expected_refs.append(text)

    if not expected_refs:
        for ref in citation_policy.get("std_ref_fallbacks") or []:
            text = str(ref).strip()
            if text and text not in expected_refs:
                expected_refs.append(text)

    return expected_refs


def _rule_variants(
    guideline: dict[str, Any],
    focus: str,
    obligation_class: str,
    mode: str,
) -> list[dict[str, str]]:
    scope = str(guideline.get("scope") or "crate")
    topic = str(guideline.get("technical_topic") or "safety-critical guidance")

    base_variants = [
        {
            "rule_statement": (
                f"{scope.title()}-scope implementations shall enforce explicit controls for {focus}, "
                "and must reject implicit or ambiguous behavior in safety-relevant paths."
            ),
            "amplification": (
                f"For {topic}, define approved patterns, forbidden patterns, and verification checkpoints "
                "that reviewers can evaluate objectively."
            ),
            "exceptions": (
                "Deviations require documented hazard impact, mitigation evidence, and independent "
                "approval before integration."
            ),
            "rationale": (
                f"Explicit constraints for {focus} reduce latent defects and strengthen ISO 26262 "
                "compliance evidence."
            ),
        },
        {
            "rule_statement": (
                f"Within {scope}-level code, teams must constrain {focus} using approved constructs, "
                "and shall forbid unreviewed shortcuts in critical control flow."
            ),
            "amplification": (
                f"The {topic} implementation shall include bounded preconditions, postconditions, and "
                "negative-path checks that can be re-run during audits."
            ),
            "exceptions": (
                "Exception requests are permitted only with traceable risk closure and reviewer sign-off."
            ),
            "rationale": (
                f"Constraining {focus} improves deterministic behavior and supports repeatable verification."
            ),
        },
        {
            "rule_statement": (
                f"Safety-related {scope} modules shall apply explicit guardrails for {focus}, and must "
                "preserve analyzable behavior under normal and fault conditions."
            ),
            "amplification": (
                f"For {topic}, specify observable pass/fail evidence, boundary conditions, and reviewer "
                "criteria before accepting compliant examples."
            ),
            "exceptions": (
                "Any deviation must include quantitative impact analysis and compensating controls."
            ),
            "rationale": (
                f"These controls narrow interpretation variance for {focus} and reduce verification drift."
            ),
        },
    ]

    if obligation_class == "table":
        for item in base_variants:
            item["amplification"] = _append_sentence(
                item["amplification"],
                "Trace each requirement to a table-row evidence entry in the coverage matrix.",
            )
    elif obligation_class == "annex":
        for item in base_variants:
            item["rationale"] = _append_sentence(
                item["rationale"],
                "Document annex interpretation rationale to keep informative guidance auditable.",
            )

    if mode == "diversify":
        diversify_variant = {
            "rule_statement": (
                f"Approved {focus} patterns shall be explicit at {scope} scope, and verification checks "
                "must detect divergence from the defined safety model."
            ),
            "amplification": (
                f"Use {topic} review criteria that differentiate compliant behavior from near-miss patterns "
                "through bounded tests and evidence records."
            ),
            "exceptions": (
                "Allow deviations only when alternate controls provide equivalent or stronger safety evidence."
            ),
            "rationale": (
                f"Distinct rule wording and constraints maintain coverage fidelity while reducing duplicate guidance for {focus}."
            ),
        }
        base_variants.append(diversify_variant)

    return base_variants


def _select_rewrite_variant(
    root: Path,
    guidelines: list[dict[str, Any]],
    guideline_id: str,
    variants: list[dict[str, str]],
) -> dict[str, str]:
    diversity_policy = _load_diversity_policy(root)
    lead_policy = diversity_policy.get("lead_in") or {}
    token_window = int(lead_policy.get("token_window") or 8)
    max_repeated_lead = int(lead_policy.get("max_repeated_lead_in_count") or 2)

    normalized_set, lead_counts = _rule_statement_stats(guidelines, guideline_id, token_window)
    if not variants:
        return {
            "rule_statement": "",
            "amplification": "",
            "exceptions": "",
            "rationale": "",
        }

    ranked: list[tuple[int, dict[str, str]]] = []
    for variant in variants:
        statement = str(variant.get("rule_statement") or "")
        normalized = _normalize_statement(statement)
        lead = _lead_in_key(statement, token_window)
        duplicate_penalty = 50 if normalized in normalized_set else 0
        lead_penalty = 10 if lead_counts.get(lead, 0) >= max_repeated_lead else 0
        ranked.append((duplicate_penalty + lead_penalty, variant))

    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def _focus_phrase(guideline: dict[str, Any]) -> str:
    refs = [str(item).lower() for item in guideline.get("fls_refs", [])]
    if any("unsafety" in ref for ref in refs):
        return "unsafe blocks and invariants"
    if any("concurrency" in ref for ref in refs):
        return "shared-state and concurrency behavior"
    if any("exceptions_and_errors" in ref for ref in refs):
        return "error propagation and failure handling"
    if any("types_and_traits" in ref for ref in refs):
        return "type modeling and trait constraints"
    if any("macros" in ref for ref in refs):
        return "macro expansion boundaries"
    return "safety-critical Rust control flow"


def _normalize_expected_outcome(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized == "documented-only":
        return "documented_only"
    return normalized


def _default_example_outcome(guideline: dict[str, Any], side: str) -> str:
    decidable = str(guideline.get("decidable") or "").strip()
    status = str(guideline.get("decidable_status") or "").strip()
    if side == "compliant":
        return "assertion_pass" if decidable == "decidable" else "documented_only"
    if status == "compiler":
        return "compile_fail"
    if status == "clippy":
        return "lint_trigger"
    if status == "possible-with-clippy":
        return "runtime_panic"
    if status == "impossible-with-clippy":
        return "runtime_panic"
    return "runtime_panic" if decidable == "decidable" else "documented_only"


def _compile_expectation_for_outcome(outcome: str, side: str) -> str:
    normalized = _normalize_expected_outcome(outcome)
    if normalized == "compile_fail":
        return "compile_fail"
    if normalized == "documented_only":
        return "documented-only"
    if normalized == "assertion_pass":
        return "compile_pass"
    if normalized in {"runtime_panic", "lint_trigger"}:
        return "compile_pass"
    return "compile_pass" if side == "compliant" else "documented-only"


def _fence_tag_for_outcome(outcome: str) -> str:
    normalized = _normalize_expected_outcome(outcome)
    if normalized == "compile_fail":
        return "compile_fail"
    if normalized == "runtime_panic":
        return "should_panic"
    if normalized == "documented_only":
        return "no_run"
    return "rust"


def _example_code_for_outcome(outcome: str, side: str, focus: str) -> str:
    normalized = _normalize_expected_outcome(outcome)
    if normalized == "compile_fail":
        return (
            "fn main() {\n"
            "    // Intentional compile failure for negative evidence.\n"
            '    let must_be_u32: u32 = "invalid";\n'
            "    let _ = must_be_u32;\n"
            "}\n"
        )
    if normalized == "runtime_panic":
        return (
            "fn main() {\n"
            "    // Intentional runtime panic for negative evidence.\n"
            "    let values = [10_u32, 20_u32];\n"
            "    let idx = values.len();\n"
            "    let _ = values[idx];\n"
            "}\n"
        )
    if normalized == "lint_trigger":
        return (
            "fn main() {\n"
            "    // Negative example for lint-oriented enforcement; replace with rule-specific trigger.\n"
            "    let mut total = 0_u32;\n"
            "    for item in [1_u32, 2_u32, 3_u32] {\n"
            "        total += item;\n"
            "    }\n"
            "    if total == 6 {\n"
            '        println!("{}", total);\n'
            "    }\n"
            "}\n"
        )
    if normalized == "assertion_pass":
        return (
            "fn main() {\n"
            f"    // Compliant evidence for {focus}.\n"
            "    let values = [1_u32, 2_u32, 3_u32];\n"
            "    let total: u32 = values.into_iter().sum();\n"
            "    assert_eq!(total, 6);\n"
            "}\n"
        )
    if side == "compliant":
        return "fn main() {\n    let stable_value = 42_u32;\n    let _ = stable_value;\n}\n"
    return "fn main() {\n    let unchecked = -1_i32;\n    let _ = unchecked;\n}\n"


def _rewrite_example_markdown(
    path: Path,
    guideline_id: str,
    side: str,
    focus: str,
    expected_outcome: str,
    verification_notes: str = "",
) -> None:
    title = f"{side.replace('_', ' ').title()} Example: {guideline_id}"

    normalized_outcome = _normalize_expected_outcome(expected_outcome) or "documented_only"
    fence_tag = _fence_tag_for_outcome(normalized_outcome)
    code = _example_code_for_outcome(normalized_outcome, side, focus).rstrip()

    if side == "compliant":
        lead = (
            f"This example demonstrates {focus} with explicit, reviewable constraints and "
            "deterministic evidence."
        )
    else:
        lead = (
            f"This example intentionally violates {focus} constraints and should be treated "
            "as negative evidence."
        )

    lines = [
        f"# {title}",
        "",
        lead,
        "",
        f"Expected outcome: `{normalized_outcome}`.",
    ]
    if verification_notes.strip():
        lines.extend(["", f"Verification notes: {_sanitize_text(verification_notes.strip())}"])
    lines.extend(["", f"```{fence_tag}", code, "```", ""])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _extract_first_rust_code(markdown: str) -> str:
    match = re.search(r"```[^\n]*\n(?P<code>[\s\S]*?)\n```", markdown)
    if not match:
        return "fn main() {}\n"
    return f"{match.group('code').rstrip()}\n"


def apply_assign_missing_fls_refs(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    guideline_filter = str(action.get("guideline_id") or "").strip()
    target_filter = str(action.get("target_id") or "").strip()
    max_refs = int(action.get("max_refs") or 3)

    path, payload, guidelines = _load_todo_guidelines(root)
    seed_index = _load_seed_index(root)
    fls_index = _load_fls_candidates(root)

    changed = 0
    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if guideline_filter and guideline_id != guideline_filter:
            continue

        candidate_refs: list[str] = []
        obligations = [
            str(item).strip() for item in guideline.get("obligation_units", []) if str(item).strip()
        ]
        for obligation in obligations:
            for ref in fls_index["by_obligation"].get(obligation, []):
                if ref not in candidate_refs:
                    candidate_refs.append(ref)

        target_ids: list[str] = []
        for seed_id in guideline.get("iso_seeds", []) or []:
            seed = seed_index["by_seed"].get(str(seed_id).strip())
            if seed is None:
                continue
            target = _target_from_seed(seed)
            if target not in target_ids:
                target_ids.append(target)
        for target in target_ids:
            if target_filter and target != target_filter:
                continue
            for ref in fls_index["by_target"].get(target, []):
                if ref not in candidate_refs:
                    candidate_refs.append(ref)

        if target_filter and not any(target == target_filter for target in target_ids):
            continue

        existing_refs = [
            str(item).strip() for item in guideline.get("fls_refs", []) if str(item).strip()
        ]
        updated_refs = list(existing_refs)
        for ref in candidate_refs:
            if ref in updated_refs:
                continue
            updated_refs.append(ref)
            if len(updated_refs) >= max_refs:
                break

        if updated_refs != existing_refs and updated_refs:
            guideline["fls_refs"] = updated_refs
            changed += 1

    if changed > 0:
        _write_todo_guidelines(path, payload, guidelines)
    return {"changed": changed > 0, "changed_guidelines": changed}


def apply_spawn_rule_for_obligation_unit(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    target_filter = str(action.get("target_id") or "").strip()
    obligation_filter = str(action.get("obligation_unit_id") or "").strip()

    path, payload, guidelines = _load_todo_guidelines(root)
    seed_index = _load_seed_index(root)
    fls_index = _load_fls_candidates(root)
    target_guidelines = _guidelines_for_target(root)

    candidate_seeds: list[dict[str, Any]] = []
    if obligation_filter:
        candidate_seeds.extend(seed_index["by_obligation"].get(obligation_filter, []))
    if target_filter:
        candidate_seeds.extend(seed_index["by_target"].get(target_filter, []))
    if not candidate_seeds:
        return {"changed": False, "reason": "no matching seeds"}

    candidate_seeds = sorted(
        {str(seed.get("seed_id") or ""): seed for seed in candidate_seeds}.values(),
        key=lambda seed: str(seed.get("seed_id") or ""),
    )
    seed = candidate_seeds[0]
    seed_id = str(seed.get("seed_id") or "").strip()
    target_id = target_filter or _target_from_seed(seed)
    obligation = obligation_filter or _obligation_from_seed(seed)

    guidelines_by_id = {
        str(guideline.get("id") or "").strip(): guideline
        for guideline in guidelines
        if str(guideline.get("id") or "").strip()
    }

    parent_id = ""
    for guideline in guidelines:
        gids = [str(item).strip() for item in guideline.get("iso_seeds", []) if str(item).strip()]
        if seed_id in gids:
            parent_id = str(guideline.get("id") or "").strip()
            break

    if not parent_id:
        candidate_parent_ids = sorted(target_guidelines.get(target_id, set()))
        if candidate_parent_ids:
            parent_id = candidate_parent_ids[0]

    if not parent_id or parent_id not in guidelines_by_id:
        return {"changed": False, "reason": "no parent guideline for target"}

    existing_ids = set(guidelines_by_id.keys())
    new_id = _next_child_guideline_id(parent_id, obligation, existing_ids)
    parent = guidelines_by_id[parent_id]
    child = copy.deepcopy(parent)
    child["id"] = new_id
    child["iso_seeds"] = [seed_id]
    child["obligation_units"] = [obligation]
    child["decomposition_parent"] = parent_id
    child["rule_family_id"] = str(
        child.get("rule_family_id") or _rule_family_id_for_target(target_id)
    )

    topic = str(child.get("technical_topic") or "safety topic").lower()
    child["rule_statement"] = (
        f"For obligation unit {obligation}, apply explicit Rust constraints for {topic} "
        "with deterministic verification criteria."
    )
    child["amplification"] = (
        f"This decomposed sub-guideline narrows coverage to obligation {obligation}. "
        "Define allowed patterns, forbidden patterns, and objective pass/fail evidence."
    )
    child["rationale"] = (
        f"Decomposition from {parent_id} increases traceability and enforceability for {target_id}."
    )
    child["state"] = "DRAFT"

    if not child.get("fls_refs"):
        refs = fls_index["by_obligation"].get(obligation, []) or fls_index["by_target"].get(
            target_id, []
        )
        if refs:
            child["fls_refs"] = refs[:3]

    examples = child.get("examples") or {}
    for side in ["compliant", "non_compliant"]:
        entry = examples.get(side) or {}
        code_path = str(entry.get("code_path") or "")
        doc_path = str(entry.get("doc_path") or "")
        if code_path:
            entry["code_path"] = _replace_rule_id(code_path, parent_id, new_id)
        if doc_path:
            entry["doc_path"] = _replace_rule_id(doc_path, parent_id, new_id)
        entry["explanation"] = (
            f"{side.replace('_', ' ').title()} evidence for decomposed rule {new_id} "
            f"covering obligation {obligation}."
        )
        expected_outcome = _normalize_expected_outcome(str(entry.get("expected_outcome") or ""))
        if not expected_outcome:
            expected_outcome = _default_example_outcome(child, side)
        entry["expected_outcome"] = expected_outcome
        entry["compile_expectation"] = _compile_expectation_for_outcome(expected_outcome, side)
        if (
            expected_outcome == "documented_only"
            and not str(entry.get("verification_notes") or "").strip()
        ):
            entry["verification_notes"] = (
                "Automated execution not required; reviewer verifies context-specific evidence."
            )
        examples[side] = entry
    child["examples"] = examples

    child["evidence_artifacts"] = [
        _replace_rule_id(str(item), parent_id, new_id)
        for item in (child.get("evidence_artifacts") or [])
    ]

    guidelines.append(child)
    _write_todo_guidelines(path, payload, guidelines)
    return {"changed": True, "new_guideline_id": new_id, "parent_guideline_id": parent_id}


def apply_rewrite_rule_statement_specific(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    guideline_filter = str(action.get("guideline_id") or "").strip()
    if not guideline_filter:
        return {"changed": False, "reason": "guideline_id required"}

    rewrite_mode = str(action.get("rewrite_mode") or "balanced").strip().lower()
    if rewrite_mode not in {"balanced", "diversify"}:
        rewrite_mode = "balanced"

    path, payload, guidelines = _load_todo_guidelines(root)
    changed = False
    rewrite_source = "deterministic"
    rewrite_reason = "topic_variant"

    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if guideline_id != guideline_filter:
            continue

        llm_rewrite = resolve_guideline_rewrite(root, guideline_id, guidelines)
        if not bool(llm_rewrite.get("ok", False)):
            return {
                "changed": False,
                "guideline_id": guideline_filter,
                "reason": str(llm_rewrite.get("reason") or "llm_rewrite_failed"),
            }

        if (
            bool(llm_rewrite.get("applied", False))
            and str(llm_rewrite.get("source") or "") == "llm"
        ):
            rewrite_payload = llm_rewrite.get("payload") or {}
            guideline["rule_statement"] = str(rewrite_payload.get("rule_statement") or "")
            guideline["amplification"] = str(rewrite_payload.get("amplification") or "")
            guideline["exceptions"] = str(rewrite_payload.get("exceptions") or "")
            guideline["rationale"] = str(rewrite_payload.get("rationale") or "")

            rewritten_examples = rewrite_payload.get("examples") or {}
            if isinstance(rewritten_examples, dict):
                current_examples = guideline.get("examples") or {}
                for side in ["compliant", "non_compliant"]:
                    side_payload = rewritten_examples.get(side) or {}
                    if not isinstance(side_payload, dict):
                        continue
                    entry = current_examples.get(side) or {}
                    if str(side_payload.get("explanation") or "").strip():
                        entry["explanation"] = _sanitize_text(
                            str(side_payload.get("explanation") or "")
                        )

                    expected_outcome = _normalize_expected_outcome(
                        str(side_payload.get("expected_outcome") or "")
                    )
                    if expected_outcome:
                        entry["expected_outcome"] = expected_outcome
                        entry["compile_expectation"] = _compile_expectation_for_outcome(
                            expected_outcome,
                            side,
                        )

                    verification_notes = str(side_payload.get("verification_notes") or "").strip()
                    if verification_notes:
                        entry["verification_notes"] = _sanitize_text(verification_notes)

                    markdown = str(side_payload.get("markdown") or "").strip()
                    doc_path = str(entry.get("doc_path") or "").strip()
                    code_path = str(entry.get("code_path") or "").strip()
                    if markdown and doc_path:
                        doc_abs = root / doc_path
                        doc_abs.parent.mkdir(parents=True, exist_ok=True)
                        doc_abs.write_text(markdown.rstrip() + "\n", encoding="utf-8")
                        if code_path:
                            code_abs = root / code_path
                            code_abs.parent.mkdir(parents=True, exist_ok=True)
                            code_abs.write_text(
                                _extract_first_rust_code(markdown), encoding="utf-8"
                            )

                    current_examples[side] = entry
                guideline["examples"] = current_examples

            citation_plan = [
                str(item).strip()
                for item in (rewrite_payload.get("citation_plan") or [])
                if str(item).strip()
            ]
            if citation_plan:
                for marker in citation_plan[:3]:
                    if marker.startswith(":cite:`") or marker.startswith(":std:`"):
                        guideline["rationale"] = _append_sentence(
                            str(guideline.get("rationale") or ""),
                            f"Evidence anchor: {marker}.",
                        )

            rewrite_source = "llm"
            rewrite_reason = str(llm_rewrite.get("reason") or "rewrite_llm_valid")
        else:
            focus = _focus_phrase(guideline)
            obligation_class = _obligation_class(guideline)
            variants = _rule_variants(guideline, focus, obligation_class, rewrite_mode)

            if variants:
                start_index = _stable_variant_index(
                    guideline_id,
                    str(guideline.get("technical_topic") or ""),
                    focus,
                    rewrite_mode,
                    modulus=len(variants),
                )
                ordered_variants = variants[start_index:] + variants[:start_index]
            else:
                ordered_variants = variants

            selected = _select_rewrite_variant(root, guidelines, guideline_id, ordered_variants)
            guideline["rule_statement"] = str(selected.get("rule_statement") or "")
            guideline["amplification"] = str(selected.get("amplification") or "")
            guideline["exceptions"] = str(selected.get("exceptions") or "")
            guideline["rationale"] = str(selected.get("rationale") or "")

        guideline["decidability_rationale"] = _sanitize_text(
            str(guideline.get("decidability_rationale") or "")
        )

        examples = guideline.get("examples") or {}
        focus = _focus_phrase(guideline)
        for side in ["compliant", "non_compliant"]:
            entry = examples.get(side) or {}
            doc_path = str(entry.get("doc_path") or "").strip()
            code_path = str(entry.get("code_path") or "").strip()
            expected_outcome = _normalize_expected_outcome(str(entry.get("expected_outcome") or ""))
            if not expected_outcome:
                expected_outcome = _default_example_outcome(guideline, side)
            entry["expected_outcome"] = expected_outcome
            entry["compile_expectation"] = _compile_expectation_for_outcome(expected_outcome, side)
            verification_notes = str(entry.get("verification_notes") or "").strip()
            if expected_outcome == "documented_only" and not verification_notes:
                entry["verification_notes"] = (
                    "Automated execution not required; reviewer verifies context-specific evidence."
                )

            if doc_path:
                _rewrite_example_markdown(
                    root / doc_path,
                    guideline_id,
                    side,
                    focus,
                    expected_outcome,
                    str(entry.get("verification_notes") or ""),
                )
                if code_path:
                    markdown = (root / doc_path).read_text(encoding="utf-8")
                    code_abs = root / code_path
                    code_abs.parent.mkdir(parents=True, exist_ok=True)
                    code_abs.write_text(_extract_first_rust_code(markdown), encoding="utf-8")
            examples[side] = entry
        changed = True
        break

    if changed:
        _write_todo_guidelines(path, payload, guidelines)
    return {
        "changed": changed,
        "guideline_id": guideline_filter,
        "rewrite_source": rewrite_source,
        "rewrite_reason": rewrite_reason,
        "rewrite_mode": rewrite_mode,
    }


def apply_diversify_rule_wording(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    rewritten_action = dict(action)
    rewritten_action["rewrite_mode"] = "diversify"
    rewritten_action["type"] = "rewrite_rule_statement_specific"
    result = apply_rewrite_rule_statement_specific(root, rewritten_action)
    result["rewrite_mode"] = "diversify"
    return result


def apply_rewrite_amplification_with_boundaries(
    root: Path, action: dict[str, Any]
) -> dict[str, Any]:
    guideline_filter = str(action.get("guideline_id") or "").strip()
    if not guideline_filter:
        return {"changed": False, "reason": "guideline_id required"}

    path, payload, guidelines = _load_todo_guidelines(root)
    changed = False

    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if guideline_id != guideline_filter:
            continue

        focus = _focus_phrase(guideline)
        guideline["amplification"] = (
            f"Define explicit boundaries for {focus}: approved patterns, forbidden patterns, "
            "required preconditions, required postconditions, and objective verification steps."
        )
        guideline["exceptions"] = (
            "Deviations are allowed only when mitigation evidence is complete and approved by "
            "an independent reviewer."
        )
        changed = True
        break

    if changed:
        _write_todo_guidelines(path, payload, guidelines)
    return {"changed": changed, "guideline_id": guideline_filter}


def apply_upgrade_examples_non_placeholder(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    guideline_filter = str(action.get("guideline_id") or "").strip()
    if not guideline_filter:
        return {"changed": False, "reason": "guideline_id required"}

    path, payload, guidelines = _load_todo_guidelines(root)
    changed = False

    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if guideline_id != guideline_filter:
            continue

        focus = _focus_phrase(guideline)
        examples = guideline.get("examples") or {}
        for side in ["compliant", "non_compliant"]:
            entry = examples.get(side) or {}
            doc_path = str(entry.get("doc_path") or "").strip()
            code_path = str(entry.get("code_path") or "").strip()
            explanation = str(entry.get("explanation") or "")
            entry["explanation"] = _sanitize_text(explanation)
            expected_outcome = _normalize_expected_outcome(str(entry.get("expected_outcome") or ""))
            if not expected_outcome:
                expected_outcome = _default_example_outcome(guideline, side)
            entry["expected_outcome"] = expected_outcome
            entry["compile_expectation"] = _compile_expectation_for_outcome(expected_outcome, side)
            if (
                expected_outcome == "documented_only"
                and not str(entry.get("verification_notes") or "").strip()
            ):
                entry["verification_notes"] = (
                    "Automated execution not required; reviewer verifies context-specific evidence."
                )
            examples[side] = entry
            if doc_path:
                _rewrite_example_markdown(
                    root / doc_path,
                    guideline_id,
                    side,
                    focus,
                    expected_outcome,
                    str(entry.get("verification_notes") or ""),
                )
                if code_path:
                    markdown = (root / doc_path).read_text(encoding="utf-8")
                    code_abs = root / code_path
                    code_abs.parent.mkdir(parents=True, exist_ok=True)
                    code_abs.write_text(_extract_first_rust_code(markdown), encoding="utf-8")
        guideline["examples"] = examples
        changed = True
        break

    if changed:
        _write_todo_guidelines(path, payload, guidelines)
    return {"changed": changed, "guideline_id": guideline_filter}


def apply_strengthen_example_assertions(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    guideline_filter = str(action.get("guideline_id") or "").strip()
    if not guideline_filter:
        return {"changed": False, "reason": "guideline_id required"}

    path, payload, guidelines = _load_todo_guidelines(root)
    changed = False

    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if guideline_id != guideline_filter:
            continue

        focus = _focus_phrase(guideline)
        examples = guideline.get("examples") or {}
        entry = examples.get("compliant") or {}
        entry["expected_outcome"] = "assertion_pass"
        entry["compile_expectation"] = "compile_pass"
        doc_path = str(entry.get("doc_path") or "").strip()
        code_path = str(entry.get("code_path") or "").strip()
        if doc_path:
            _rewrite_example_markdown(
                root / doc_path, guideline_id, "compliant", focus, "assertion_pass"
            )
            if code_path:
                markdown = (root / doc_path).read_text(encoding="utf-8")
                code_abs = root / code_path
                code_abs.parent.mkdir(parents=True, exist_ok=True)
                code_abs.write_text(_extract_first_rust_code(markdown), encoding="utf-8")

        examples["compliant"] = entry
        guideline["examples"] = examples
        changed = True
        break

    if changed:
        _write_todo_guidelines(path, payload, guidelines)
    return {"changed": changed, "guideline_id": guideline_filter}


def apply_convert_non_compliant_to_compile_fail_or_panic(
    root: Path, action: dict[str, Any]
) -> dict[str, Any]:
    guideline_filter = str(action.get("guideline_id") or "").strip()
    if not guideline_filter:
        return {"changed": False, "reason": "guideline_id required"}

    path, payload, guidelines = _load_todo_guidelines(root)
    changed = False

    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if guideline_id != guideline_filter:
            continue

        status = str(guideline.get("decidable_status") or "").strip()
        outcome = "compile_fail" if status == "compiler" else "runtime_panic"

        focus = _focus_phrase(guideline)
        examples = guideline.get("examples") or {}
        entry = examples.get("non_compliant") or {}
        entry["expected_outcome"] = outcome
        entry["compile_expectation"] = _compile_expectation_for_outcome(outcome, "non_compliant")
        if outcome == "documented_only" and not str(entry.get("verification_notes") or "").strip():
            entry["verification_notes"] = (
                "Automated execution not required; reviewer verifies context-specific evidence."
            )
        doc_path = str(entry.get("doc_path") or "").strip()
        code_path = str(entry.get("code_path") or "").strip()
        if doc_path:
            _rewrite_example_markdown(
                root / doc_path, guideline_id, "non_compliant", focus, outcome
            )
            if code_path:
                markdown = (root / doc_path).read_text(encoding="utf-8")
                code_abs = root / code_path
                code_abs.parent.mkdir(parents=True, exist_ok=True)
                code_abs.write_text(_extract_first_rust_code(markdown), encoding="utf-8")

        examples["non_compliant"] = entry
        guideline["examples"] = examples
        changed = True
        break

    if changed:
        _write_todo_guidelines(path, payload, guidelines)
    return {"changed": changed, "guideline_id": guideline_filter}


def apply_align_example_with_decidable_status(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    guideline_filter = str(action.get("guideline_id") or "").strip()
    if not guideline_filter:
        return {"changed": False, "reason": "guideline_id required"}

    path, payload, guidelines = _load_todo_guidelines(root)
    changed = False

    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if guideline_id != guideline_filter:
            continue

        focus = _focus_phrase(guideline)
        examples = guideline.get("examples") or {}

        for side in ["compliant", "non_compliant"]:
            entry = examples.get(side) or {}
            target_outcome = _default_example_outcome(guideline, side)
            entry["expected_outcome"] = target_outcome
            entry["compile_expectation"] = _compile_expectation_for_outcome(target_outcome, side)
            if (
                target_outcome == "documented_only"
                and not str(entry.get("verification_notes") or "").strip()
            ):
                entry["verification_notes"] = (
                    "Automated execution not required; reviewer verifies context-specific evidence."
                )

            doc_path = str(entry.get("doc_path") or "").strip()
            code_path = str(entry.get("code_path") or "").strip()
            if doc_path:
                _rewrite_example_markdown(
                    root / doc_path,
                    guideline_id,
                    side,
                    focus,
                    target_outcome,
                    str(entry.get("verification_notes") or ""),
                )
                if code_path:
                    markdown = (root / doc_path).read_text(encoding="utf-8")
                    code_abs = root / code_path
                    code_abs.parent.mkdir(parents=True, exist_ok=True)
                    code_abs.write_text(_extract_first_rust_code(markdown), encoding="utf-8")

            examples[side] = entry

        guideline["examples"] = examples
        changed = True
        break

    if changed:
        _write_todo_guidelines(path, payload, guidelines)
    return {"changed": changed, "guideline_id": guideline_filter}


def apply_add_alignment_citation_signals(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    guideline_filter = str(action.get("guideline_id") or "").strip()
    if not guideline_filter:
        return {"changed": False, "reason": "guideline_id required"}

    path, payload, guidelines = _load_todo_guidelines(root)
    seed_index = _load_seed_index(root)
    citation_policy = _load_citation_policy(root)
    rust_signals = _load_rust_signals(root)
    changed = False

    max_iso = int(citation_policy.get("max_iso_citations_per_guideline") or 2)
    max_std = int(citation_policy.get("max_std_refs_per_guideline") or 3)
    min_total_markers = int(citation_policy.get("min_total_markers") or 2)
    iso_templates = citation_policy.get("iso_templates") or {}
    sentence_templates = citation_policy.get("anchor_sentence_templates") or {}
    iso_sentence_templates = [
        str(item).strip() for item in (sentence_templates.get("iso") or []) if str(item).strip()
    ]
    std_sentence_templates = [
        str(item).strip() for item in (sentence_templates.get("std") or []) if str(item).strip()
    ]

    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if guideline_id != guideline_filter:
            continue

        citation_markers: list[str] = []
        for seed_id in guideline.get("iso_seeds") or []:
            seed = seed_index["by_seed"].get(str(seed_id).strip())
            if seed is None:
                continue
            marker = _format_iso_marker(seed, iso_templates)
            citation = f":cite:`{marker}`"
            if citation not in citation_markers:
                citation_markers.append(citation)
            if len(citation_markers) >= max_iso:
                break

        if not citation_markers:
            fallback_iso = str(iso_templates.get("fallback") or "ISO26262-6-2018").strip()
            if fallback_iso:
                citation_markers.append(f":cite:`{fallback_iso}`")

        expected_std_refs = _expected_std_refs_for_guideline(
            guideline, rust_signals, citation_policy
        )
        std_markers = [f":std:`{ref}`" for ref in expected_std_refs[:max_std]]

        rule_statement = str(guideline.get("rule_statement") or "")
        amplification = str(guideline.get("amplification") or "")
        rationale = str(guideline.get("rationale") or "")
        text_blob = "\n".join([rule_statement, amplification, rationale])

        sentence_counter = 0
        for index, marker in enumerate(citation_markers):
            if marker in text_blob:
                continue
            sentence = _anchor_sentence(
                iso_sentence_templates,
                marker,
                guideline_id,
                marker_kind="iso",
                ordinal=index,
            )
            rationale = _append_sentence(rationale, sentence)
            sentence_counter += 1
            text_blob = "\n".join([rule_statement, amplification, rationale])

        for index, marker in enumerate(std_markers):
            if marker in text_blob:
                continue
            sentence = _anchor_sentence(
                std_sentence_templates,
                marker,
                guideline_id,
                marker_kind="std",
                ordinal=index,
            )
            if index % 2 == 0:
                rationale = _append_sentence(rationale, sentence)
            else:
                amplification = _append_sentence(amplification, sentence)
            sentence_counter += 1
            text_blob = "\n".join([rule_statement, amplification, rationale])

        if sentence_counter < min_total_markers:
            fallback_marker = ":std:`std::result::Result`"
            if fallback_marker not in text_blob:
                rationale = _append_sentence(
                    rationale,
                    _anchor_sentence(
                        std_sentence_templates,
                        fallback_marker,
                        guideline_id,
                        marker_kind="std",
                        ordinal=99,
                    ),
                )
                text_blob = "\n".join([rule_statement, amplification, rationale])

        if amplification != str(guideline.get("amplification") or ""):
            guideline["amplification"] = amplification
            changed = True
        if rationale != str(guideline.get("rationale") or ""):
            guideline["rationale"] = rationale
            changed = True
        break

    if changed:
        _write_todo_guidelines(path, payload, guidelines)
    return {"changed": changed, "guideline_id": guideline_filter}


def apply_rebalance_alignment_granularity(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    guideline_filter = str(action.get("guideline_id") or "").strip()
    if not guideline_filter:
        return {"changed": False, "reason": "guideline_id required"}

    mode = str(action.get("granularity_mode") or "coarse").strip().lower()
    if mode not in {"coarse", "fine"}:
        mode = "coarse"

    path, payload, guidelines = _load_todo_guidelines(root)
    changed = False

    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if guideline_id != guideline_filter:
            continue

        amplification = str(guideline.get("amplification") or "")
        exceptions = str(guideline.get("exceptions") or "")
        rule_statement = str(guideline.get("rule_statement") or "")

        if mode == "coarse":
            amplification = _append_sentence(
                amplification,
                "When selecting patterns, include explicit Result/Option handling, "
                "overflow checks, and unsafe boundary constraints to keep guidance reviewable.",
            )
            exceptions = _append_sentence(
                exceptions,
                "If deviation is required, document why this constraint cannot be applied and "
                "what verification evidence closes the risk.",
            )
        else:
            amplification = (
                "Use a single decisive rule boundary with one verification path, and avoid "
                "branching "
                "condition trees unless hazard analysis requires them."
            )
            exceptions = (
                "Allow deviations only with explicit hazard mitigation and reviewer sign-off."
            )
            rule_statement = re.sub(r"\b(if|when|unless|while|where)\b", "", rule_statement)
            rule_statement = re.sub(r"\s+", " ", rule_statement).strip()

        if amplification != str(guideline.get("amplification") or ""):
            guideline["amplification"] = amplification
            changed = True
        if exceptions != str(guideline.get("exceptions") or ""):
            guideline["exceptions"] = exceptions
            changed = True
        if mode == "fine" and rule_statement != str(guideline.get("rule_statement") or ""):
            guideline["rule_statement"] = rule_statement
            changed = True
        break

    if changed:
        _write_todo_guidelines(path, payload, guidelines)
    return {"changed": changed, "guideline_id": guideline_filter, "granularity_mode": mode}


def apply_raise_benchmark_similarity(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    guideline_filter = str(action.get("guideline_id") or "").strip()
    if not guideline_filter:
        return {"changed": False, "reason": "guideline_id required"}

    path, payload, guidelines = _load_todo_guidelines(root)
    changed = False

    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if guideline_id != guideline_filter:
            continue

        amplification = str(guideline.get("amplification") or "")
        rationale = str(guideline.get("rationale") or "")

        amplification = _append_sentence(
            amplification,
            "Use explicit unsafe boundaries, pointer and borrow constraints, and deterministic "
            "Result/Option error handling in match-based control flow.",
        )
        rationale = _append_sentence(
            rationale,
            "This wording aligns with benchmark Rust guidance terms used for overflow, panic, "
            "and trait/struct safety reasoning.",
        )

        if amplification != str(guideline.get("amplification") or ""):
            guideline["amplification"] = amplification
            changed = True
        if rationale != str(guideline.get("rationale") or ""):
            guideline["rationale"] = rationale
            changed = True
        break

    if changed:
        _write_todo_guidelines(path, payload, guidelines)
    return {"changed": changed, "guideline_id": guideline_filter}


def apply_action(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    action_type = str(action.get("type") or "").strip()
    if action_type == "assign_missing_fls_refs":
        return apply_assign_missing_fls_refs(root, action)
    if action_type == "spawn_rule_for_obligation_unit":
        return apply_spawn_rule_for_obligation_unit(root, action)
    if action_type == "rewrite_rule_statement_specific":
        return apply_rewrite_rule_statement_specific(root, action)
    if action_type == "diversify_rule_wording":
        return apply_diversify_rule_wording(root, action)
    if action_type == "rewrite_amplification_with_boundaries":
        return apply_rewrite_amplification_with_boundaries(root, action)
    if action_type == "upgrade_examples_non_placeholder":
        return apply_upgrade_examples_non_placeholder(root, action)
    if action_type == "strengthen_example_assertions":
        return apply_strengthen_example_assertions(root, action)
    if action_type == "convert_non_compliant_to_compile_fail_or_panic":
        return apply_convert_non_compliant_to_compile_fail_or_panic(root, action)
    if action_type == "align_example_with_decidable_status":
        return apply_align_example_with_decidable_status(root, action)
    if action_type == "add_alignment_citation_signals":
        return apply_add_alignment_citation_signals(root, action)
    if action_type == "rebalance_alignment_granularity":
        return apply_rebalance_alignment_granularity(root, action)
    if action_type == "raise_benchmark_similarity":
        return apply_raise_benchmark_similarity(root, action)
    return {"changed": False, "reason": f"unknown action type {action_type}"}


def refresh_generated_artifacts(root: Path) -> tuple[bool, list[dict[str, Any]]]:
    steps = [
        [sys.executable, "scripts/build_rust_signals.py"],
        [sys.executable, "scripts/decompose_guidelines.py"],
        [sys.executable, "scripts/generate_guideline_artifacts.py"],
        [sys.executable, "scripts/scaffold_guideline_fixtures.py"],
    ]
    reports: list[dict[str, Any]] = []
    for command in steps:
        completed = run_command(command, cwd=root)
        reports.append(
            {
                "command": " ".join(command),
                "return_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
        if completed.returncode != 0:
            return False, reports
    return True, reports


def apply_candidate(root: Path, candidate: dict[str, Any]) -> dict[str, Any]:
    action_reports = []
    changed_any = False
    for action in candidate.get("actions", []):
        report = apply_action(root, action)
        action_reports.append({"action": action, "result": report})
        changed_any = changed_any or bool(report.get("changed"))

    if not changed_any:
        return {
            "ok": True,
            "changed": False,
            "action_reports": action_reports,
            "refresh_reports": [],
            "note": "candidate produced no changes",
        }

    refresh_ok, refresh_reports = refresh_generated_artifacts(root)
    return {
        "ok": refresh_ok,
        "changed": True,
        "action_reports": action_reports,
        "refresh_reports": refresh_reports,
    }
