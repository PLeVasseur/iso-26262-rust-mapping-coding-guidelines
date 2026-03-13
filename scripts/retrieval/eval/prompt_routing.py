from __future__ import annotations

from typing import Any

HYBRID_FUSION_ROUTING_OFF = "off"
HYBRID_FUSION_ROUTING_BEST_PRACTICE_V1 = "best-practice-v1"


def classify_prompt_family(prompt: dict[str, Any]) -> str:
    prompt_id = str(prompt.get("prompt_id", "")).strip().lower()
    query_text = str(prompt.get("query_text", "")).strip().lower()
    relevant_terms = " ".join(
        str(term).strip().lower() for term in prompt.get("relevant_terms", [])
    )
    haystack = " ".join(part for part in (prompt_id, query_text, relevant_terms) if part)

    unsafe_control_flow_tokens = (
        "unsafe",
        "undefined",
        "pointer",
        "alias",
        "lifetime",
        "control-flow",
        "control flow",
        "pattern",
        "match",
        "branch",
    )
    error_handling_tokens = (
        "error",
        "panic",
        "unwrap",
        "expect",
        "result",
        "defensive",
    )

    if any(token in haystack for token in unsafe_control_flow_tokens):
        return "unsafe_control_flow"
    if any(token in haystack for token in error_handling_tokens):
        return "error_handling"
    return "general_semantics"


def resolve_hybrid_fusion_method_for_case(
    *,
    mode: str,
    prompt: dict[str, Any],
    default_method: str,
    routing_policy: str,
) -> tuple[str, str]:
    if str(mode).strip() != "hybrid":
        return str(default_method).strip(), "not_hybrid"

    normalized_policy = str(routing_policy).strip().lower() or HYBRID_FUSION_ROUTING_OFF
    if normalized_policy == HYBRID_FUSION_ROUTING_OFF:
        return str(default_method).strip(), "routing_off"

    prompt_family = classify_prompt_family(prompt)
    if normalized_policy == HYBRID_FUSION_ROUTING_BEST_PRACTICE_V1:
        if prompt_family == "unsafe_control_flow":
            return "weighted-v2", prompt_family
        return "rrf-v1", prompt_family

    return str(default_method).strip(), "unknown_routing_policy"
