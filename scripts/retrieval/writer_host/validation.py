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
        construct_scope = output.get("construct_scope")
        if not isinstance(construct_scope, list):
            violations.append("construct_scope_not_list")
        claim_map = output.get("claim_to_evidence_map")
        if not isinstance(claim_map, list):
            violations.append("claim_to_evidence_map_not_list")
        else:
            for index, claim in enumerate(claim_map):
                if not isinstance(claim, dict):
                    violations.append(f"claim_not_object:{index}")
                    continue
                claim_id = str(claim.get("claim_id", "")).strip()
                prompt_id = str(output.get("prompt_id", "")).strip()
                if prompt_id and not re.match(rf"^{re.escape(prompt_id)}::claim::\d+$", claim_id):
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

    return violations
