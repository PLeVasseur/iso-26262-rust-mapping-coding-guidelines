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
    extract_json_blob,
    read_json,
    read_yaml,
    repo_root,
    run_command,
    utc_now,
    write_json,
)

DEFAULT_POLICY: dict[str, Any] = {
    "version": 1,
    "enabled": True,
    "max_selected_candidates": 2,
    "llm": {
        "enabled": False,
        "fallback_to_deterministic": True,
        "command": [],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve controller candidate selection")
    parser.add_argument(
        "--decision-packet",
        type=Path,
        required=True,
        help="Path to decision packet JSON",
    )
    parser.add_argument(
        "--iteration-dir",
        type=Path,
        required=True,
        help="Iteration artifact directory",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("config/controller_decision_policy.yaml"),
    )
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def _normalize_policy(raw_policy: dict[str, Any]) -> dict[str, Any]:
    policy = {
        "version": int(raw_policy.get("version") or DEFAULT_POLICY["version"]),
        "enabled": bool(raw_policy.get("enabled", DEFAULT_POLICY["enabled"])),
        "max_selected_candidates": int(
            raw_policy.get(
                "max_selected_candidates",
                DEFAULT_POLICY["max_selected_candidates"],
            )
            or 1
        ),
    }
    llm_raw = raw_policy.get("llm") or {}
    command_value = llm_raw.get("command")
    command: list[str]
    if isinstance(command_value, list):
        command = [str(item) for item in command_value if str(item).strip()]
    elif isinstance(command_value, str) and command_value.strip():
        command = shlex.split(command_value)
    else:
        command = []

    policy["llm"] = {
        "enabled": bool(llm_raw.get("enabled", DEFAULT_POLICY["llm"]["enabled"])),
        "fallback_to_deterministic": bool(
            llm_raw.get(
                "fallback_to_deterministic",
                DEFAULT_POLICY["llm"]["fallback_to_deterministic"],
            )
        ),
        "command": command,
    }
    return policy


def load_policy(root: Path, policy_path: Path) -> dict[str, Any]:
    path = root / policy_path
    if not path.exists():
        return dict(DEFAULT_POLICY)
    payload = read_yaml(path) or {}
    if not isinstance(payload, dict):
        return dict(DEFAULT_POLICY)
    return _normalize_policy(payload)


def _bounded_deficits(deficits: list[dict[str, Any]], limit: int = 25) -> list[dict[str, Any]]:
    ranked = sorted(
        deficits,
        key=lambda item: (
            {
                "critical": 0,
                "high": 1,
                "medium": 2,
                "low": 3,
            }.get(str(item.get("severity") or "low"), 4),
            -float(item.get("distance_to_pass") or 0.0),
            str(item.get("deficit_id") or ""),
        ),
    )
    trimmed: list[dict[str, Any]] = []
    for item in ranked[:limit]:
        trimmed.append(
            {
                "deficit_id": str(item.get("deficit_id") or ""),
                "type": str(item.get("type") or ""),
                "severity": str(item.get("severity") or "low"),
                "guideline_id": str(item.get("guideline_id") or ""),
                "target_id": str(item.get("target_id") or ""),
                "distance_to_pass": float(item.get("distance_to_pass") or 0.0),
            }
        )
    return trimmed


def observation_summary(observation: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "runtime_failures",
        "policy_failures",
        "iso_obligation_gap_count",
        "target_fanout_gap_count",
        "fls_span_gap_count",
        "fls_chapter_gap_count",
        "quality_gap_count",
        "placeholder_gap_count",
        "example_gap_count",
        "known_good_alignment_gap_count",
        "known_good_alignment_average",
        "iso_obligation_coverage",
        "fls_chapter_coverage",
        "quality_pass_ratio",
        "total_deficit_count",
    ]
    return {key: observation.get(key) for key in keys}


def build_decision_packet(
    session_id: str,
    iteration: int,
    observation: dict[str, Any],
    candidates: list[dict[str, Any]],
    suppressed_signatures: set[str],
    historical_signatures: set[str],
    alignment_overrides: dict[str, Any],
    policy_context: dict[str, Any],
) -> dict[str, Any]:
    candidate_rows = []
    for candidate in candidates:
        candidate_rows.append(
            {
                "candidate_id": str(candidate.get("candidate_id") or ""),
                "actions": candidate.get("actions") or [],
                "pre_score": float(candidate.get("pre_score") or 0.0),
                "risk_penalty": float(candidate.get("risk_penalty") or 0.0),
                "mutation_footprint_estimate": int(
                    candidate.get("mutation_footprint_estimate") or 0
                ),
                "bundle_signature": str(candidate.get("bundle_signature") or ""),
                "expected_lane_deltas": candidate.get("expected_lane_deltas") or {},
            }
        )

    deficits = [dict(item) for item in (observation.get("deficits") or [])]
    return {
        "version": 1,
        "session_id": session_id,
        "iteration": iteration,
        "generated_at": utc_now(),
        "observation_summary": observation_summary(observation),
        "deficits_summary": _bounded_deficits(deficits),
        "candidates": candidate_rows,
        "suppressed_signatures": sorted(suppressed_signatures),
        "historical_signatures": sorted(historical_signatures),
        "alignment_overrides": alignment_overrides,
        "policy_context": policy_context,
    }


def validate_payload(root: Path, schema_rel: str, payload: Any) -> list[str]:
    schema = read_json(root / schema_rel)
    validator = Draft202012Validator(schema)
    return [error.message for error in validator.iter_errors(payload)]


def _deterministic_candidate_ids(packet: dict[str, Any], max_selected: int) -> list[str]:
    candidates = packet.get("candidates") or []
    candidate_ids = [str(item.get("candidate_id") or "") for item in candidates]
    candidate_ids = [item for item in candidate_ids if item]
    if max_selected <= 0:
        return candidate_ids
    return candidate_ids[:max_selected]


def _substitute_placeholders(token: str, values: dict[str, str]) -> str:
    updated = token
    for key, value in values.items():
        updated = updated.replace(f"{{{key}}}", value)
    return updated


def _resolve_llm_command(policy: dict[str, Any]) -> list[str]:
    env_command = os.environ.get("CONTROLLER_DECISION_COMMAND", "").strip()
    if env_command:
        return shlex.split(env_command)
    return [str(item) for item in (policy.get("llm") or {}).get("command", [])]


def _confidence_bucket(value: float) -> str:
    bounded = max(0.0, min(1.0, float(value)))
    if bounded < (1.0 / 3.0):
        return "low"
    if bounded < (2.0 / 3.0):
        return "medium"
    return "high"


def _normalize_confidence(value: Any) -> str | None:
    if isinstance(value, int | float):
        return _confidence_bucket(float(value))

    if not isinstance(value, str):
        return None

    text = value.strip().lower()
    if not text:
        return None
    if text in {"low", "medium", "high"}:
        return text

    aliases = {
        "very low": "low",
        "very_high": "high",
        "very high": "high",
        "med": "medium",
        "mid": "medium",
    }
    if text in aliases:
        return aliases[text]

    try:
        numeric = float(text)
        return _confidence_bucket(numeric)
    except ValueError:
        return None


def _normalize_llm_payload(raw_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(raw_payload)

    selected = payload.get("selected_candidate_ids")
    if isinstance(selected, str):
        payload["selected_candidate_ids"] = [selected]
    elif isinstance(selected, list):
        payload["selected_candidate_ids"] = [str(item) for item in selected if str(item)]

    rejected = payload.get("rejected_candidate_ids")
    if isinstance(rejected, str):
        payload["rejected_candidate_ids"] = [rejected]
    elif isinstance(rejected, list):
        payload["rejected_candidate_ids"] = [str(item) for item in rejected if str(item)]

    risk_notes = payload.get("risk_notes")
    if isinstance(risk_notes, str):
        payload["risk_notes"] = [risk_notes]
    elif isinstance(risk_notes, list):
        payload["risk_notes"] = [str(item) for item in risk_notes]

    fallback_recommended = payload.get("fallback_recommended")
    if isinstance(fallback_recommended, str):
        normalized = fallback_recommended.strip().lower()
        payload["fallback_recommended"] = normalized in {"1", "true", "yes", "on"}

    confidence = _normalize_confidence(payload.get("confidence"))
    if confidence is not None:
        payload["confidence"] = confidence

    return payload


def _apply_fallback(
    resolution: dict[str, Any],
    reason: str,
    rationale: str,
    llm_error: str,
    fallback_to_deterministic: bool,
) -> dict[str, Any]:
    resolution["selection_source"] = "fallback"
    resolution["rationale"] = rationale
    if llm_error:
        resolution["llm_error"] = llm_error

    if fallback_to_deterministic:
        resolution["resolution_reason"] = reason
        return resolution

    resolution["resolution_reason"] = f"fallback_disallowed:{reason}"
    resolution["ordered_candidate_ids"] = []
    resolution["selected_candidate_ids"] = []
    resolution["rejected_candidate_ids"] = []
    resolution["fallback_recommended"] = True
    return resolution


def _parse_llm_payload(
    raw_stdout: str,
    output_path: Path,
) -> tuple[bool, dict[str, Any], str]:
    if raw_stdout.strip():
        try:
            parsed = extract_json_blob(raw_stdout)
            if isinstance(parsed, dict):
                return True, parsed, ""
            return False, {}, "llm stdout did not contain a JSON object"
        except Exception as exc:  # noqa: BLE001
            if not output_path.exists():
                return False, {}, f"failed parsing llm stdout JSON: {exc}"

    if output_path.exists():
        try:
            parsed = read_json(output_path)
            if isinstance(parsed, dict):
                return True, parsed, ""
            return False, {}, "llm output file did not contain a JSON object"
        except Exception as exc:  # noqa: BLE001
            return False, {}, f"failed reading llm output file: {exc}"

    return False, {}, "llm command produced no parseable JSON"


def resolve_candidate_selection(
    root: Path,
    packet: dict[str, Any],
    iteration_dir: Path,
    policy_path: Path = Path("config/controller_decision_policy.yaml"),
) -> dict[str, Any]:
    iteration_dir.mkdir(parents=True, exist_ok=True)

    packet_errors = validate_payload(root, "schemas/controller_decision_packet.schema.json", packet)
    packet_path = iteration_dir / "decision_packet.json"
    write_json(packet_path, packet)

    policy = load_policy(root, policy_path)
    max_selected = int(policy.get("max_selected_candidates") or 1)
    deterministic_ids = _deterministic_candidate_ids(packet, max_selected)

    resolution = {
        "selection_source": "deterministic",
        "resolution_reason": "deterministic_policy",
        "ordered_candidate_ids": deterministic_ids,
        "selected_candidate_ids": deterministic_ids,
        "rejected_candidate_ids": [],
        "rationale": "deterministic candidate rank order",
        "confidence": "medium",
        "fallback_recommended": False,
        "llm_invoked": False,
        "llm_output_valid": False,
        "policy_path": str(policy_path),
        "generated_at": utc_now(),
        "packet_validation_errors": packet_errors,
    }

    if packet_errors:
        resolution["selection_source"] = "fallback"
        resolution["resolution_reason"] = "packet_schema_invalid"
        return resolution

    if not bool(policy.get("enabled", True)):
        return resolution

    llm_cfg = policy.get("llm") or {}
    if not bool(llm_cfg.get("enabled", False)):
        resolution["resolution_reason"] = "llm_disabled"
        return resolution

    command_tokens = _resolve_llm_command(policy)
    if not command_tokens:
        resolution["selection_source"] = "fallback"
        resolution["resolution_reason"] = "llm_command_missing"
        return resolution

    llm_output_path = iteration_dir / "llm_decision.output.json"
    placeholders = {
        "session_id": str(packet.get("session_id") or ""),
        "iteration": str(packet.get("iteration") or ""),
        "decision_packet": str(packet_path),
        "decision_output": str(llm_output_path),
        "repo_root": str(root),
    }

    rendered = [_substitute_placeholders(token, placeholders) for token in command_tokens]
    has_packet_placeholder = any("{decision_packet}" in token for token in command_tokens)
    if not has_packet_placeholder:
        rendered.append(str(packet_path))

    completed = run_command(rendered, cwd=root)
    raw_payload = {
        "command": rendered,
        "return_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "generated_at": utc_now(),
    }
    raw_path = iteration_dir / "llm_decision.raw.json"
    write_json(raw_path, raw_payload)

    resolution["llm_invoked"] = True
    fallback_to_deterministic = bool(llm_cfg.get("fallback_to_deterministic", True))

    if completed.returncode != 0:
        return _apply_fallback(
            resolution,
            "llm_command_failed",
            "llm command failed; fallback to deterministic order",
            completed.stderr or completed.stdout,
            fallback_to_deterministic,
        )

    parsed_ok, llm_payload, parse_error = _parse_llm_payload(
        completed.stdout,
        llm_output_path,
    )
    if not parsed_ok:
        return _apply_fallback(
            resolution,
            "llm_output_unparseable",
            "llm output not parseable; fallback to deterministic order",
            parse_error,
            fallback_to_deterministic,
        )

    llm_payload = _normalize_llm_payload(llm_payload)

    decision_errors = validate_payload(
        root,
        "schemas/controller_llm_decision.schema.json",
        llm_payload,
    )
    if decision_errors:
        return _apply_fallback(
            resolution,
            "llm_output_schema_invalid",
            "llm output schema invalid; fallback to deterministic order",
            "; ".join(decision_errors),
            fallback_to_deterministic,
        )

    candidate_set = {
        str(item.get("candidate_id") or "")
        for item in (packet.get("candidates") or [])
        if str(item.get("candidate_id") or "")
    }
    selected_ids = [
        str(item)
        for item in llm_payload.get("selected_candidate_ids", [])
        if str(item)
    ]
    selected_ids = list(dict.fromkeys(selected_ids))

    unknown = [item for item in selected_ids if item not in candidate_set]
    if unknown:
        return _apply_fallback(
            resolution,
            "llm_selected_unknown_candidates",
            "llm selected unknown candidates; fallback to deterministic order",
            f"unknown candidate ids: {', '.join(sorted(unknown))}",
            fallback_to_deterministic,
        )

    if bool(llm_payload.get("fallback_recommended", False)):
        fallback_result = _apply_fallback(
            resolution,
            "llm_requested_fallback",
            str(llm_payload.get("rationale") or "fallback requested"),
            "",
            fallback_to_deterministic,
        )
        fallback_result["confidence"] = str(llm_payload.get("confidence") or "low")
        return fallback_result

    if not selected_ids:
        return _apply_fallback(
            resolution,
            "llm_selected_none",
            "llm selected no candidates; fallback to deterministic order",
            "",
            fallback_to_deterministic,
        )

    ordered_ids = selected_ids[:max_selected]
    validated_path = iteration_dir / "llm_decision.validated.json"
    write_json(validated_path, llm_payload)

    resolution.update(
        {
            "selection_source": "llm",
            "resolution_reason": "llm_valid",
            "ordered_candidate_ids": ordered_ids,
            "selected_candidate_ids": ordered_ids,
            "rejected_candidate_ids": llm_payload.get("rejected_candidate_ids") or [],
            "rationale": str(llm_payload.get("rationale") or "llm-selected ordering"),
            "confidence": str(llm_payload.get("confidence") or "medium"),
            "fallback_recommended": False,
            "llm_output_valid": True,
        }
    )
    return resolution


def main() -> int:
    args = parse_args()
    root = repo_root()
    packet_path = root / args.decision_packet
    if not packet_path.exists():
        print(f"[controller-decision][error] missing packet: {packet_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL

    packet = read_json(packet_path)
    if not isinstance(packet, dict):
        print("[controller-decision][error] decision packet must be a JSON object")
        return EXIT_POLICY_FAIL

    result = resolve_candidate_selection(
        root,
        packet,
        root / args.iteration_dir,
        policy_path=args.policy,
    )

    output_path = args.json_output or Path(".cache/controller/decision_resolution.json")
    write_json(root / output_path, result)
    print(
        "[controller-decision] "
        f"source={result.get('selection_source')} "
        f"reason={result.get('resolution_reason')} "
        f"selected={len(result.get('ordered_candidate_ids') or [])}"
    )
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
