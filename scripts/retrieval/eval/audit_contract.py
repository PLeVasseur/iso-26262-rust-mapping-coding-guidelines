from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json_block(markdown_text: str) -> dict[str, Any]:
    match = JSON_BLOCK_RE.search(markdown_text)
    if match is None:
        raise ValueError("Audit report must contain a fenced ```json block")
    payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        raise ValueError("Audit JSON block must decode to an object")
    return payload


def validate_audit_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    required_scalars = (
        "schema_version",
        "phase",
        "candidate_id",
        "comparator_candidate_id",
    )
    for key in required_scalars:
        if str(payload.get(key, "")).strip() == "":
            errors.append(f"missing required field: {key}")

    weak_prompt_ids = payload.get("weak_prompt_ids")
    if not isinstance(weak_prompt_ids, list):
        errors.append("weak_prompt_ids must be a list")
        weak_prompt_ids = []
    if len(weak_prompt_ids) != 10:
        errors.append(f"weak_prompt_ids must contain 10 prompts (got {len(weak_prompt_ids)})")

    findings = payload.get("findings")
    if not isinstance(findings, list):
        errors.append("findings must be a list")
        findings = []

    findings_by_prompt: dict[str, dict[str, Any]] = {}
    for finding in findings:
        if not isinstance(finding, dict):
            errors.append("finding entry must be an object")
            continue
        prompt_id = str(finding.get("prompt_id", "")).strip()
        if not prompt_id:
            errors.append("finding missing prompt_id")
            continue
        findings_by_prompt[prompt_id] = finding

        chunks = finding.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            errors.append(f"finding {prompt_id} must include non-empty chunks list")
            continue
        for index, chunk in enumerate(chunks, start=1):
            if not isinstance(chunk, dict):
                errors.append(f"finding {prompt_id} chunk#{index} must be object")
                continue
            label = str(chunk.get("label", "")).strip().lower()
            severity = str(chunk.get("severity", "")).strip().lower()
            rationale = str(chunk.get("rationale", "")).strip()
            if label not in {"on-target", "partial", "off-target"}:
                errors.append(f"finding {prompt_id} chunk#{index} invalid label '{label}'")
            if severity not in {"high", "medium", "low"}:
                errors.append(f"finding {prompt_id} chunk#{index} invalid severity '{severity}'")
            if not rationale:
                errors.append(f"finding {prompt_id} chunk#{index} missing rationale")

    for prompt_id in weak_prompt_ids:
        token = str(prompt_id).strip()
        if token and token not in findings_by_prompt:
            errors.append(f"missing finding entry for weak prompt {token}")

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary must be an object")
    else:
        for key in (
            "high_count",
            "medium_count",
            "low_count",
            "citation_readiness_delta",
            "recommendation",
        ):
            if key not in summary:
                errors.append(f"summary missing field: {key}")

    return errors


def validate_audit_markdown(path: Path) -> list[str]:
    payload = extract_json_block(path.read_text(encoding="utf-8"))
    return validate_audit_payload(payload)
