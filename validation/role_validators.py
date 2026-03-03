"""Per-role output validators for writer pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class RoleViolation:
    check: str
    message: str
    severity: str = "error"


def _validate_amplification_output(
    output: dict[str, Any],
    convention_spec: dict[str, Any],
    std_lookup: dict[str, str],
    construct_terms: list[str],
) -> list[RoleViolation]:
    violations: list[RoleViolation] = []
    text = str(output.get("guideline_amplification_text", ""))

    for term in construct_terms:
        short_name = term.split("::")[-1].strip()
        if not short_name:
            continue
        fq_path = std_lookup.get(short_name)
        if not fq_path:
            continue
        bare_pattern = rf"(?<!:std:)(?<!:)`{re.escape(short_name)}`"
        if re.search(bare_pattern, text):
            violations.append(
                RoleViolation(
                    check="std_role_missing",
                    message=(
                        f"Type `{short_name}` should use :std: role: "
                        f":std:`{fq_path}`. See convention_spec.std_role_convention."
                    ),
                )
            )

    placement_policy = str(convention_spec.get("citation_placement_policy", "llm_authored"))
    if placement_policy == "renderer_injected":
        citation_keys = output.get("amplification_citation_keys", [])
        if not isinstance(citation_keys, list) or not citation_keys:
            violations.append(
                RoleViolation(
                    check="citation_keys_empty",
                    message=(
                        "amplification_citation_keys list must be non-empty. "
                        "Provide at least one citation key for renderer-injected placement."
                    ),
                )
            )
    else:
        if not re.search(r":cite:`[^`]+`", text):
            violations.append(
                RoleViolation(
                    check="cite_missing",
                    message=(
                        "Guideline body text must include at least one :cite:`KEY` inline. "
                        "Place it after the sentence making the evidenced claim."
                    ),
                )
            )

    strength = str(output.get("normative_strength", "")).strip().lower()
    if strength not in {"shall", "should"}:
        violations.append(
            RoleViolation(
                check="normative_strength_invalid",
                message=f"Normative strength must be 'shall' or 'should', got '{strength}'.",
            )
        )

    return violations


def _validate_metadata_output(
    output: dict[str, Any],
    convention_spec: dict[str, Any],
    prompt_id: str,
) -> list[RoleViolation]:
    _ = prompt_id
    violations: list[RoleViolation] = []
    title = str(output.get("title", "")).strip()
    if title.startswith("Guideline for "):
        examples = convention_spec.get("title_convention", {}).get("examples", [])
        violations.append(
            RoleViolation(
                check="title_generic",
                message=(
                    f"Title '{title}' is generic. Must be a descriptive English sentence "
                    f"stating the requirement. Examples from exemplars: {examples}"
                ),
            )
        )
    if len(title) < 10:
        violations.append(
            RoleViolation(
                check="title_too_short",
                message=(
                    f"Title is too short ({len(title)} chars). "
                    "Must be a complete, descriptive sentence."
                ),
            )
        )

    tags = output.get("tags", [])
    if isinstance(tags, list):
        for raw_tag in tags:
            tag = str(raw_tag).strip()
            if tag in {"core_docs", "rust_reference", "s0"}:
                values_seen = convention_spec.get("tag_convention", {}).get("values_seen", [])
                violations.append(
                    RoleViolation(
                        check="tag_pipeline_internal",
                        message=(
                            f"Tag '{tag}' is a pipeline-internal name. "
                            f"Use descriptive tags. Examples: {values_seen}"
                        ),
                    )
                )
            if re.match(r"table1-\d", tag):
                violations.append(
                    RoleViolation(
                        check="tag_iso_reference",
                        message=(
                            f"Tag '{tag}' is an ISO table reference. "
                            "Use descriptive tags like 'atomics' or 'concurrency'."
                        ),
                    )
                )

    category = str(output.get("category", "")).strip().lower()
    if category == "mandatory":
        violations.append(
            RoleViolation(
                check="category_mandatory",
                message=(
                    "Category 'mandatory' is used sparingly. Consider 'advisory' "
                    "or 'required' unless this rule is truly mandatory."
                ),
                severity="warning",
            )
        )

    bib_rows = output.get("bibliography_rows", [])
    if isinstance(bib_rows, list):
        for idx, row in enumerate(bib_rows):
            if not isinstance(row, dict):
                continue
            key = str(row.get("citation_key", "")).strip()
            if not key:
                violations.append(
                    RoleViolation(
                        check="bibliography_missing_key",
                        message=f"Bibliography row {idx} has no citation_key.",
                    )
                )

    return violations


def _validate_example_output(
    output: dict[str, Any],
    convention_spec: dict[str, Any],
    construct_terms: list[str],
) -> list[RoleViolation]:
    _ = convention_spec
    violations: list[RoleViolation] = []

    non_compl = str(output.get("non_compliant_code", ""))
    compl = str(output.get("compliant_code", ""))
    if len(non_compl.strip()) < 10:
        violations.append(
            RoleViolation(
                check="non_compliant_code_empty",
                message=(
                    "Non-compliant code is empty or trivial. Must demonstrate the specific hazard."
                ),
            )
        )
    if len(compl.strip()) < 10:
        violations.append(
            RoleViolation(
                check="compliant_code_empty",
                message=("Compliant code is empty or trivial. Must demonstrate the mitigation."),
            )
        )

    if "unsafe" in non_compl:
        intent = str(output.get("non_compliant_miri_intent", "none")).strip()
        if intent == "none":
            violations.append(
                RoleViolation(
                    check="miri_intent_missing",
                    message=(
                        "Non-compliant code contains `unsafe` but no miri intent declared. "
                        "Set non_compliant_miri_intent to 'expect_ub' or 'check'."
                    ),
                )
            )

    all_text = " ".join(
        [
            non_compl,
            compl,
            str(output.get("non_compliant_narrative", "")),
            str(output.get("compliant_narrative", "")),
        ]
    ).lower()
    construct_found = any(
        term.lower().split("::")[-1] in all_text for term in construct_terms if term.strip()
    )
    if construct_terms and not construct_found:
        violations.append(
            RoleViolation(
                check="example_not_construct_specific",
                message=(
                    f"Examples do not reference target constructs: {construct_terms}. "
                    "Examples must demonstrate the specific construct, not generic Rust patterns."
                ),
            )
        )

    return violations


def validate_role_output(
    role_name: str,
    output: dict[str, Any],
    convention_spec: dict[str, Any],
    std_lookup: dict[str, str],
    construct_terms: list[str],
    prompt_id: str,
) -> list[RoleViolation]:
    """Dispatch to role-specific validator using trusted prompt_id context."""
    if role_name == "amplification_author":
        return _validate_amplification_output(output, convention_spec, std_lookup, construct_terms)
    if role_name == "metadata_citation_curator":
        return _validate_metadata_output(output, convention_spec, prompt_id)
    if role_name == "example_author":
        return _validate_example_output(output, convention_spec, construct_terms)
    return []
