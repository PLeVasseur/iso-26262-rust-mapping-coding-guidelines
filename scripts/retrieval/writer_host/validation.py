from __future__ import annotations

import json
import re
from typing import Any


def validate_role_output(
    *,
    role_name: str,
    output: dict[str, Any],
    role_contract: dict[str, Any],
    evidence_ids: set[str],
) -> list[str]:
    violations: list[str] = []
    required_output_schema = role_contract.get("required_output_schema")
    required_fields = []
    if isinstance(required_output_schema, dict):
        required_fields = list(required_output_schema.get("required") or [])
    for field in required_fields:
        key = str(field)
        if key not in output:
            violations.append(f"missing_required:{key}")

    text_blob = json.dumps(output, sort_keys=True)
    for pattern in list(role_contract.get("forbidden_patterns") or []):
        p = str(pattern).strip().lower()
        if p and p in text_blob.lower():
            violations.append(f"forbidden_pattern:{p}")

    if role_name == "evidence_synthesizer":
        prompt_id = str(output.get("prompt_id", "")).strip()
        if not prompt_id:
            violations.append("missing_prompt_id")
        construct_scope = output.get("construct_scope")
        if not isinstance(construct_scope, list):
            violations.append("construct_scope_not_list")
        claim_map = output.get("claim_to_evidence_map")
        if not isinstance(claim_map, list):
            violations.append("claim_to_evidence_map_not_list")
        elif not claim_map:
            violations.append("claim_to_evidence_map_empty")
        else:
            for index, claim in enumerate(claim_map):
                if not isinstance(claim, dict):
                    violations.append(f"claim_not_object:{index}")
                    continue
                claim_id = str(claim.get("claim_id", "")).strip()
                if not re.match(rf"^{re.escape(prompt_id)}::claim::\d+$", claim_id):
                    violations.append(f"claim_id_format:{index}")
                refs = claim.get("evidence_refs")
                if not isinstance(refs, list) or not refs:
                    violations.append(f"missing_evidence_refs:{index}")
                    continue
                for ref_index, ref in enumerate(refs):
                    if not isinstance(ref, dict):
                        violations.append(f"evidence_ref_not_object:{index}:{ref_index}")
                        continue
                    evidence_id = str(ref.get("evidence_id", "")).strip()
                    if not evidence_id:
                        violations.append(f"missing_evidence_id:{index}:{ref_index}")
                    elif evidence_ids and evidence_id not in evidence_ids:
                        violations.append(f"unknown_evidence_id:{index}:{ref_index}")

    if role_name in {"amplification_author", "example_author", "rationale_author"}:
        citation_field = {
            "amplification_author": "amplification_citation_keys",
            "example_author": "example_citation_keys",
            "rationale_author": "rationale_citation_keys",
        }[role_name]
        citation_keys = output.get(citation_field)
        if not isinstance(citation_keys, list):
            violations.append(f"{citation_field}_not_list")
        elif not citation_keys:
            violations.append(f"{citation_field}_empty")
        else:
            for idx, key in enumerate(citation_keys):
                if not str(key).strip():
                    violations.append(f"{citation_field}_blank:{idx}")

    return violations


def validate_target_bundle(
    *,
    target_id: str,
    outputs: dict[str, dict[str, Any]],
) -> list[str]:
    violations: list[str] = []
    synth_raw = outputs.get("evidence_synthesizer")
    synth: dict[str, Any] = synth_raw if isinstance(synth_raw, dict) else {}
    metadata_raw = outputs.get("metadata_citation_curator")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    synth_evidence_ids = {
        str(value).strip() for value in list(synth.get("evidence_ids") or []) if str(value).strip()
    }
    citation_map_raw = metadata.get("citation_key_map")
    citation_map: dict[str, str] = {}
    if isinstance(citation_map_raw, dict):
        citation_map = {
            str(key).strip(): str(value).strip()
            for key, value in citation_map_raw.items()
            if str(key).strip()
        }

    for role_name, field_name in (
        ("amplification_author", "amplification_citation_keys"),
        ("example_author", "example_citation_keys"),
        ("rationale_author", "rationale_citation_keys"),
    ):
        role_output = outputs.get(role_name)
        if not isinstance(role_output, dict):
            continue
        citation_keys = role_output.get(field_name)
        if not isinstance(citation_keys, list):
            violations.append(f"cross_role:{role_name}:{field_name}_not_list")
            continue
        for key in citation_keys:
            key_text = str(key).strip()
            if not key_text:
                violations.append(f"cross_role:{role_name}:empty_citation_key")
                continue
            if key_text in citation_map:
                continue
            if key_text in synth_evidence_ids:
                continue
            violations.append(f"cross_role:{role_name}:missing_citation_map:{key_text}")

    for citation_key, evidence_id in citation_map.items():
        if not evidence_id:
            violations.append(f"cross_role:metadata:empty_evidence_id:{citation_key}")
            continue
        if (
            synth_evidence_ids
            and evidence_id not in synth_evidence_ids
            and citation_key not in synth_evidence_ids
        ):
            violations.append(f"grounding:metadata:evidence_not_in_synth:{citation_key}")

    claim_map = synth.get("claim_to_evidence_map")
    if isinstance(claim_map, list) and not claim_map:
        violations.append("merge:evidence_synthesizer_empty_claim_map")
    if not isinstance(claim_map, list):
        violations.append("merge:evidence_synthesizer_claim_map_missing")

    return violations
