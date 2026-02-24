from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SLICES = {"issue_identification", "resolution_identification"}
MODES = {"lexical", "semantic", "hybrid"}
TARGET_SCOPES = {"any", "qnx", "vxworks", "embedded"}
OVERRIDABLE_MIN_METRICS = {
    "precision_at_k",
    "mrr_at_k",
    "ndcg_at_k",
    "row_hit_rate",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"YAML payload at {path} must be a mapping")
    return payload


def load_eval_prompts(path: Path) -> list[dict[str, Any]]:
    payload = _load_yaml(path)
    require_extended_metadata = str(payload.get("suite_id", "")).startswith("core_docs_")
    prompts = payload.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        raise RuntimeError("Retrieval eval file must define non-empty prompts list")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_prompt in prompts:
        if not isinstance(raw_prompt, dict):
            raise RuntimeError("Each prompt entry must be a mapping")

        prompt_id = str(raw_prompt.get("prompt_id", "")).strip()
        if not prompt_id:
            raise RuntimeError("Prompt missing prompt_id")
        if prompt_id in seen_ids:
            raise RuntimeError(f"Duplicate prompt_id: {prompt_id}")
        seen_ids.add(prompt_id)

        slice_name = str(raw_prompt.get("slice", "")).strip()
        if slice_name not in SLICES:
            raise RuntimeError(f"Prompt {prompt_id} has invalid slice {slice_name}")

        query_text = str(raw_prompt.get("query_text", "")).strip()
        if not query_text:
            raise RuntimeError(f"Prompt {prompt_id} missing query_text")

        raw_modes = raw_prompt.get("modes")
        if not isinstance(raw_modes, list) or not raw_modes:
            raise RuntimeError(f"Prompt {prompt_id} must define non-empty modes")
        modes = [str(mode).strip() for mode in raw_modes]
        if any(mode not in MODES for mode in modes):
            raise RuntimeError(f"Prompt {prompt_id} includes unknown mode")

        expected_row_markers = [
            str(value).strip().lower()
            for value in (raw_prompt.get("expected_row_markers") or [])
            if str(value).strip()
        ]
        relevant_statement_ids = [
            str(value).strip() for value in (raw_prompt.get("relevant_statement_ids") or [])
        ]
        relevant_anchor_prefixes = [
            str(value).strip() for value in (raw_prompt.get("relevant_anchor_prefixes") or [])
        ]
        relevant_terms = [
            str(value).strip().lower() for value in (raw_prompt.get("relevant_terms") or [])
        ]
        hard_negative_statement_ids = [
            str(value).strip() for value in (raw_prompt.get("hard_negative_statement_ids") or [])
        ]
        expected_item_kinds = [
            str(value).strip().lower()
            for value in (raw_prompt.get("expected_item_kinds") or [])
            if str(value).strip()
        ]
        required_evidence_fields = [
            str(value).strip()
            for value in (raw_prompt.get("required_evidence_fields") or [])
            if str(value).strip()
        ]
        target_scope = str(raw_prompt.get("target_scope", "any")).strip().lower()
        if target_scope not in TARGET_SCOPES:
            raise RuntimeError(f"Prompt {prompt_id} has invalid target_scope {target_scope}")
        raw_min_metrics = raw_prompt.get("min_metrics") or {}
        if not isinstance(raw_min_metrics, dict):
            raise RuntimeError(f"Prompt {prompt_id} min_metrics must be a mapping")

        min_metrics: dict[str, dict[str, float]] = {}
        for mode_name, metric_mapping in raw_min_metrics.items():
            mode_key = str(mode_name).strip()
            if mode_key not in MODES:
                raise RuntimeError(f"Prompt {prompt_id} min_metrics has unknown mode {mode_key}")
            if not isinstance(metric_mapping, dict):
                raise RuntimeError(
                    f"Prompt {prompt_id} min_metrics for {mode_key} must be a mapping"
                )

            normalized_metrics: dict[str, float] = {}
            for metric_name, metric_value in metric_mapping.items():
                metric_key = str(metric_name).strip()
                if metric_key not in OVERRIDABLE_MIN_METRICS:
                    raise RuntimeError(
                        f"Prompt {prompt_id} min_metrics has unsupported metric {metric_key}"
                    )
                normalized_metrics[metric_key] = float(metric_value)
            if normalized_metrics:
                min_metrics[mode_key] = normalized_metrics

        if not (
            expected_row_markers
            or relevant_statement_ids
            or relevant_anchor_prefixes
            or relevant_terms
        ):
            raise RuntimeError(
                f"Prompt {prompt_id} must define at least one relevance signal "
                "(row markers, statement ids, anchor prefixes, or terms)"
            )
        if require_extended_metadata and not expected_item_kinds:
            raise RuntimeError(f"Prompt {prompt_id} missing expected_item_kinds")
        if require_extended_metadata and not required_evidence_fields:
            raise RuntimeError(f"Prompt {prompt_id} missing required_evidence_fields")

        if not expected_item_kinds:
            expected_item_kinds = ["statement"]
        if not required_evidence_fields:
            required_evidence_fields = ["row_markers"]

        normalized.append(
            {
                "prompt_id": prompt_id,
                "category": str(raw_prompt.get("category", "")).strip().lower(),
                "slice": slice_name,
                "query_text": query_text,
                "modes": modes,
                "expected_row_markers": expected_row_markers,
                "relevant_statement_ids": relevant_statement_ids,
                "relevant_anchor_prefixes": relevant_anchor_prefixes,
                "relevant_terms": relevant_terms,
                "hard_negative_statement_ids": hard_negative_statement_ids,
                "expected_item_kinds": expected_item_kinds,
                "required_evidence_fields": required_evidence_fields,
                "target_scope": target_scope,
                "min_metrics": min_metrics,
                "semantic_focus": bool(raw_prompt.get("semantic_focus", False)),
                "expect_abstain": bool(raw_prompt.get("expect_abstain", False)),
            }
        )

    return normalized
