from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PRE_ATOM_ROLES = (
    "evidence_synthesizer",
    "editorial_planner",
)

ATOM_AUTHOR_ROLES = (
    "amplification_author",
    "example_author",
    "rationale_author",
    "metadata_citation_curator",
)

POST_ATOM_ROLES = ("editorial_curator",)

REQUIRED_ROLES = PRE_ATOM_ROLES + ATOM_AUTHOR_ROLES + POST_ATOM_ROLES


def load_contracts(contract_path: Path) -> dict[str, Any]:
    if not contract_path.exists():
        raise RuntimeError(f"writer contract file missing: {contract_path}")
    with contract_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"writer contract must be a mapping: {contract_path}")
    validate_contracts(payload)
    return payload


def validate_contracts(payload: dict[str, Any]) -> None:
    roles = payload.get("roles")
    if not isinstance(roles, dict):
        raise RuntimeError("writer contract missing roles mapping")
    for role_name in REQUIRED_ROLES:
        role_cfg = roles.get(role_name)
        if not isinstance(role_cfg, dict):
            raise RuntimeError(f"writer contract missing role: {role_name}")
        prompt_template_text = str(role_cfg.get("prompt_template_text", "")).strip()
        if not prompt_template_text:
            raise RuntimeError(f"writer contract role missing prompt text: {role_name}")
        required_output_schema = role_cfg.get("required_output_schema")
        if not isinstance(required_output_schema, dict):
            raise RuntimeError(f"writer contract role missing required_output_schema: {role_name}")
        required_fields = required_output_schema.get("required")
        if not isinstance(required_fields, list) or not required_fields:
            raise RuntimeError(f"writer contract role missing required fields: {role_name}")


def build_contract_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    roles = payload.get("roles") if isinstance(payload.get("roles"), dict) else {}
    role_rows: dict[str, Any] = {}
    for role_name in REQUIRED_ROLES:
        role_cfg = roles.get(role_name) if isinstance(roles, dict) else None
        if not isinstance(role_cfg, dict):
            continue
        role_rows[role_name] = {
            "prompt_template_id": str(role_cfg.get("prompt_template_id", "")),
            "allowed_placeholders": list(role_cfg.get("allowed_placeholders") or []),
            "required_inputs": list(role_cfg.get("required_inputs") or []),
            "required_output_fields": list(
                (role_cfg.get("required_output_schema") or {}).get("required") or []
            ),
            "forbidden_patterns": list(role_cfg.get("forbidden_patterns") or []),
        }
    return {
        "contract_version": int(payload.get("contract_version", 1) or 1),
        "roles": role_rows,
    }
