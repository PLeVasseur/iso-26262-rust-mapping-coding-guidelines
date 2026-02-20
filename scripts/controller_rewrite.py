#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from _common import (
    EXIT_POLICY_FAIL,
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    RUN_COMMAND_TIMEOUT_RETURN_CODE,
    extract_json_blob,
    load_guidelines_payload,
    read_json,
    read_yaml,
    repo_root,
    run_command,
    utc_now,
    write_json,
)
from known_good_lib import cosine_similarity


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve optional LLM rewrite for one guideline")
    parser.add_argument("--guideline-id", required=True)
    parser.add_argument("--todo-guidelines", type=Path, default=Path("data/todo_guidelines.yaml"))
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("config/controller_rewrite_policy.yaml"),
    )
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def confidence_bucket(value: float) -> str:
    bounded = max(0.0, min(1.0, float(value)))
    if bounded < (1.0 / 3.0):
        return "low"
    if bounded < (2.0 / 3.0):
        return "medium"
    return "high"


def normalize_confidence(value: Any) -> str | None:
    if isinstance(value, int | float):
        return confidence_bucket(float(value))
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"low", "medium", "high"}:
        return normalized
    try:
        return confidence_bucket(float(normalized))
    except ValueError:
        return None


def normalize_expected_outcome(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized == "documented-only":
        return "documented_only"
    return normalized


def normalize_examples_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    normalized: dict[str, Any] = {}
    for side in ["compliant", "non_compliant"]:
        side_payload = value.get(side)
        if not isinstance(side_payload, dict):
            continue

        normalized_side: dict[str, Any] = {}
        for field in ["explanation", "verification_notes", "markdown"]:
            text = str(side_payload.get(field) or "").strip()
            if text:
                normalized_side[field] = text

        expected_outcome = normalize_expected_outcome(side_payload.get("expected_outcome"))
        if expected_outcome:
            normalized_side["expected_outcome"] = expected_outcome

        if normalized_side:
            normalized[side] = normalized_side

    return normalized


def normalize_payload(raw: dict[str, Any]) -> dict[str, Any]:
    payload = dict(raw)
    for field in [
        "rule_statement",
        "amplification",
        "exceptions",
        "rationale",
        "uniqueness_rationale",
    ]:
        payload[field] = str(payload.get(field) or "").strip()

    citation_plan = payload.get("citation_plan")
    if isinstance(citation_plan, str):
        payload["citation_plan"] = [citation_plan]
    elif isinstance(citation_plan, list):
        payload["citation_plan"] = [
            str(item).strip() for item in citation_plan if str(item).strip()
        ]
    else:
        payload["citation_plan"] = []

    confidence = normalize_confidence(payload.get("confidence"))
    payload["confidence"] = confidence or "low"
    payload["examples"] = normalize_examples_payload(payload.get("examples"))
    return payload


def validate_payload_schema(root: Path, payload: dict[str, Any]) -> list[str]:
    schema = read_json(root / "schemas/controller_llm_rewrite.schema.json")
    validator = Draft202012Validator(schema)
    return [error.message for error in validator.iter_errors(payload)]


def validate_constraints(payload: dict[str, Any], constraints: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    min_words = int(constraints.get("min_rule_words") or 3)
    max_words = int(constraints.get("max_rule_words") or 100)
    require_normative = bool(constraints.get("require_normative_modal", True))
    require_verification = bool(constraints.get("require_verification_phrase", True))

    rule_words = [item for item in str(payload.get("rule_statement") or "").split() if item.strip()]
    if len(rule_words) < min_words:
        errors.append(f"rule_statement has too few words: {len(rule_words)} < {min_words}")
    if len(rule_words) > max_words:
        errors.append(f"rule_statement has too many words: {len(rule_words)} > {max_words}")

    if require_normative:
        rule_lower = str(payload.get("rule_statement") or "").lower()
        if not any(token in rule_lower for token in ["shall", "must", "avoid", "require"]):
            errors.append("rule_statement missing normative modal")

    if require_verification:
        text = "\n".join(
            [
                str(payload.get("amplification") or ""),
                str(payload.get("rationale") or ""),
                str(payload.get("exceptions") or ""),
            ]
        ).lower()
        if not any(token in text for token in ["verify", "evidence", "test", "check", "review"]):
            errors.append("rewrite text missing verification/evidence phrase")

    examples = payload.get("examples") or {}
    for side in ["compliant", "non_compliant"]:
        side_payload = examples.get(side) or {}
        expected_outcome = normalize_expected_outcome(side_payload.get("expected_outcome"))
        if not expected_outcome:
            continue
        if expected_outcome not in {
            "assertion_pass",
            "compile_fail",
            "runtime_panic",
            "lint_trigger",
            "documented_only",
        }:
            errors.append(f"examples.{side}.expected_outcome invalid: {expected_outcome}")
            continue

        if side == "compliant" and expected_outcome in {
            "compile_fail",
            "runtime_panic",
            "lint_trigger",
        }:
            errors.append("examples.compliant.expected_outcome invalid for compliant side")
        if side == "non_compliant" and expected_outcome == "assertion_pass":
            errors.append("examples.non_compliant.expected_outcome invalid for non_compliant side")

        markdown = str(side_payload.get("markdown") or "").strip()
        if markdown and "```" not in markdown:
            errors.append(f"examples.{side}.markdown missing fenced code block")

    return errors


def load_example_markdown(root: Path, guideline: dict[str, Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    examples = guideline.get("examples") or {}
    for side in ["compliant", "non_compliant"]:
        entry = examples.get(side) or {}
        doc_rel = str(entry.get("doc_path") or "").strip()
        if not doc_rel:
            continue
        doc_path = root / doc_rel
        if not doc_path.exists():
            continue
        output[side] = doc_path.read_text(encoding="utf-8")
    return output


def top_neighbors(
    guidelines: list[dict[str, Any]], guideline_id: str, max_neighbors: int
) -> list[dict[str, Any]]:
    target = None
    for guideline in guidelines:
        if str(guideline.get("id") or "").strip() == guideline_id:
            target = guideline
            break
    if target is None:
        return []

    target_text = "\n".join(
        [
            str(target.get("rule_statement") or ""),
            str(target.get("amplification") or ""),
            str(target.get("exceptions") or ""),
            str(target.get("rationale") or ""),
        ]
    )

    scored: list[dict[str, Any]] = []
    for guideline in guidelines:
        other_id = str(guideline.get("id") or "").strip()
        if not other_id or other_id == guideline_id:
            continue
        other_text = "\n".join(
            [
                str(guideline.get("rule_statement") or ""),
                str(guideline.get("amplification") or ""),
                str(guideline.get("exceptions") or ""),
                str(guideline.get("rationale") or ""),
            ]
        )
        similarity = cosine_similarity(target_text, other_text)
        scored.append(
            {
                "guideline_id": other_id,
                "similarity": round(similarity, 6),
                "rule_statement": str(guideline.get("rule_statement") or ""),
                "obligation_units": guideline.get("obligation_units") or [],
                "rule_family_id": str(guideline.get("rule_family_id") or ""),
            }
        )

    scored.sort(key=lambda item: (-float(item.get("similarity") or 0.0), item["guideline_id"]))
    return scored[:max_neighbors]


def resolve_llm_command(policy: dict[str, Any]) -> list[str]:
    env_command = os.environ.get("CONTROLLER_REWRITE_COMMAND", "").strip()
    if env_command:
        return shlex.split(env_command)

    llm_payload = policy.get("llm") or {}
    command = llm_payload.get("command")
    if isinstance(command, list):
        return [str(item) for item in command if str(item).strip()]
    if isinstance(command, str) and command.strip():
        return shlex.split(command)
    return []


def substitute_placeholders(token: str, values: dict[str, str]) -> str:
    output = token
    for key, value in values.items():
        output = output.replace(f"{{{key}}}", value)
    return output


def _rewrite_timeout_seconds(llm_cfg: dict[str, Any], default_seconds: float = 180.0) -> float:
    raw_value = llm_cfg.get("timeout_seconds", default_seconds)
    try:
        timeout_seconds = float(raw_value)
    except (TypeError, ValueError):
        timeout_seconds = default_seconds
    if timeout_seconds < 0:
        timeout_seconds = default_seconds
    return timeout_seconds


def resolve_guideline_rewrite(
    root: Path,
    guideline_id: str,
    guidelines: list[dict[str, Any]],
    policy_path: Path = Path("config/controller_rewrite_policy.yaml"),
) -> dict[str, Any]:
    policy_file = root / policy_path
    if not policy_file.exists():
        return {
            "ok": True,
            "source": "deterministic",
            "applied": False,
            "reason": "policy_missing",
        }

    policy = read_yaml(policy_file) or {}
    if not bool(policy.get("enabled", False)):
        return {
            "ok": True,
            "source": "deterministic",
            "applied": False,
            "reason": "rewrite_policy_disabled",
        }

    llm_cfg = policy.get("llm") or {}
    if not bool(llm_cfg.get("enabled", False)):
        return {
            "ok": True,
            "source": "deterministic",
            "applied": False,
            "reason": "rewrite_llm_disabled",
        }

    target = None
    for guideline in guidelines:
        if str(guideline.get("id") or "").strip() == guideline_id:
            target = guideline
            break
    if target is None:
        return {
            "ok": False,
            "source": "llm",
            "applied": False,
            "reason": "guideline_not_found",
        }

    max_neighbors = int(llm_cfg.get("max_neighbors") or 5)
    neighbors = top_neighbors(guidelines, guideline_id, max_neighbors)
    packet = {
        "version": 1,
        "generated_at": utc_now(),
        "guideline": {
            "id": guideline_id,
            "technical_topic": str(target.get("technical_topic") or ""),
            "scope": str(target.get("scope") or ""),
            "rule_family_id": str(target.get("rule_family_id") or ""),
            "obligation_units": target.get("obligation_units") or [],
            "fls_refs": target.get("fls_refs") or [],
            "rule_statement": str(target.get("rule_statement") or ""),
            "amplification": str(target.get("amplification") or ""),
            "exceptions": str(target.get("exceptions") or ""),
            "rationale": str(target.get("rationale") or ""),
            "examples": target.get("examples") or {},
            "example_markdown": load_example_markdown(root, target),
        },
        "nearest_neighbors": neighbors,
        "constraints": policy.get("constraints") or {},
    }

    output_root = root / ".cache" / "controller" / "rewrite"
    output_root.mkdir(parents=True, exist_ok=True)
    packet_path = output_root / f"{guideline_id}.packet.json"
    output_path = output_root / f"{guideline_id}.output.json"
    raw_path = output_root / f"{guideline_id}.raw.json"
    write_json(packet_path, packet)

    command_tokens = resolve_llm_command(policy)
    if not command_tokens:
        return {
            "ok": True,
            "source": "deterministic",
            "applied": False,
            "reason": "rewrite_command_missing",
        }

    placeholders = {
        "repo_root": str(root),
        "guideline_id": guideline_id,
        "rewrite_packet": str(packet_path),
        "rewrite_output": str(output_path),
    }
    command = [substitute_placeholders(token, placeholders) for token in command_tokens]
    if not any("{rewrite_packet}" in token for token in command_tokens):
        command.append(str(packet_path))

    timeout_seconds = _rewrite_timeout_seconds(llm_cfg)
    completed = run_command(
        command,
        cwd=root,
        timeout_seconds=timeout_seconds if timeout_seconds > 0 else None,
    )
    write_json(
        raw_path,
        {
            "command": command,
            "return_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "generated_at": utc_now(),
        },
    )

    fallback_to_deterministic = bool(llm_cfg.get("fallback_to_deterministic", True))
    if completed.returncode != 0:
        timeout_hit = completed.returncode == RUN_COMMAND_TIMEOUT_RETURN_CODE
        if fallback_to_deterministic:
            return {
                "ok": True,
                "source": "fallback",
                "applied": False,
                "reason": "rewrite_command_timeout" if timeout_hit else "rewrite_command_failed",
                "error": completed.stderr or completed.stdout,
            }
        return {
            "ok": False,
            "source": "llm",
            "applied": False,
            "reason": "rewrite_command_timeout" if timeout_hit else "rewrite_command_failed",
            "error": completed.stderr or completed.stdout,
        }

    parsed: dict[str, Any] | None = None
    try:
        candidate = extract_json_blob(completed.stdout)
        if isinstance(candidate, dict):
            parsed = candidate
    except Exception:  # noqa: BLE001
        parsed = None

    if parsed is None and output_path.exists():
        loaded = read_json(output_path)
        if isinstance(loaded, dict):
            parsed = loaded

    if parsed is None:
        if fallback_to_deterministic:
            return {
                "ok": True,
                "source": "fallback",
                "applied": False,
                "reason": "rewrite_output_unparseable",
            }
        return {
            "ok": False,
            "source": "llm",
            "applied": False,
            "reason": "rewrite_output_unparseable",
        }

    normalized = normalize_payload(parsed)
    schema_errors = validate_payload_schema(root, normalized)
    constraint_errors = validate_constraints(normalized, policy.get("constraints") or {})
    all_errors = [*schema_errors, *constraint_errors]
    if all_errors:
        if fallback_to_deterministic:
            return {
                "ok": True,
                "source": "fallback",
                "applied": False,
                "reason": "rewrite_output_invalid",
                "errors": all_errors,
            }
        return {
            "ok": False,
            "source": "llm",
            "applied": False,
            "reason": "rewrite_output_invalid",
            "errors": all_errors,
        }

    return {
        "ok": True,
        "source": "llm",
        "applied": True,
        "reason": "rewrite_llm_valid",
        "payload": normalized,
    }


def main() -> int:
    args = parse_args()
    root = repo_root()
    payload = load_guidelines_payload(root / args.todo_guidelines)
    guidelines = payload.get("guidelines") or []

    result = resolve_guideline_rewrite(root, args.guideline_id, guidelines, policy_path=args.policy)
    if args.json_output:
        write_json(root / args.json_output, result)

    if not result.get("ok", False):
        print(f"[controller-rewrite][error] {result.get('reason')}")
        return EXIT_RUNTIME_FAIL

    if result.get("source") == "llm" and result.get("applied", False):
        print(f"[controller-rewrite] applied guideline={args.guideline_id} source=llm")
        return EXIT_SUCCESS

    if result.get("source") == "fallback" and str(result.get("reason") or "").startswith(
        "fallback_disallowed"
    ):
        print(f"[controller-rewrite][error] {result.get('reason')}")
        return EXIT_POLICY_FAIL

    print(
        "[controller-rewrite] "
        f"guideline={args.guideline_id} source={result.get('source')} "
        f"reason={result.get('reason')}"
    )
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
