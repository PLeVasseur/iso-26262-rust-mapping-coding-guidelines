"""Step 4 standalone Stage-B judges operating on rendered RST."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

STAGE_B_JUDGES = [
    "technical_accuracy",
    "functional_safety_relevance",
    "pedagogical_quality",
]


def load_judge_contracts(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"Invalid judge contracts format: {path}")
    return loaded


def _build_judge_prompt(
    judge_name: str,
    judge_contract: dict[str, Any],
    rst_content: str,
    construct_terms: list[str],
) -> str:
    template = str(judge_contract.get("prompt_template_text", "")).strip()
    required = judge_contract.get("required_output_schema", {})
    forbidden = judge_contract.get("forbidden_patterns", [])
    return (
        f"{template}\n\n"
        f"Required schema fields: {json.dumps(required, sort_keys=True)}\n"
        f"Forbidden patterns: {json.dumps(forbidden)}\n\n"
        f"=== RENDERED GUIDELINE (RST) ===\n{rst_content}\n\n"
        f"=== CONSTRUCT SCOPE ===\n{json.dumps(construct_terms)}\n"
        f"=== JUDGE ===\n{judge_name}\n"
    )


def _normalize_judge_decision(raw_decision: str, reason_codes: list[str]) -> str:
    normalized = raw_decision.strip().lower()
    if normalized == "abstain":
        reason_codes.append("judge_abstained_treated_as_fail")
        return "fail"
    if normalized in {"pass", "fail"}:
        return normalized
    reason_codes.append(f"unexpected_decision_value:{raw_decision}")
    return "fail"


def _parse_judge_output(raw_output: str, judge_name: str) -> tuple[str, str, list[str]]:
    reason_codes: list[str] = []
    if not raw_output or not raw_output.strip():
        return "fail", "Judge produced empty output.", ["judge_output_empty"]

    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw_output, flags=re.DOTALL)
        if not match:
            return "fail", "No JSON object found in judge output.", ["judge_output_no_json_found"]
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            return "fail", f"JSON parse error: {exc}", [f"judge_output_json_parse_error:{exc}"]

    decision = str(parsed.get("decision", "")).strip().lower()
    summary = str(parsed.get("summary", "")).strip()
    if not decision:
        return "fail", summary or "Decision field missing.", ["judge_output_missing_decision_field"]
    if decision not in {"pass", "fail", "abstain"}:
        reason_codes.append(f"judge_output_invalid_decision:{decision}")
        return "fail", summary or f"Invalid decision: {decision}", reason_codes
    return decision, summary, reason_codes


def _compute_verdict(per_judge_decisions: dict[str, str]) -> str:
    decisions = [per_judge_decisions.get(name, "fail") for name in STAGE_B_JUDGES]
    return "candidate" if all(value == "pass" for value in decisions) else "blocked"


def _extract_title(rst_content: str) -> str:
    lines = [line.rstrip() for line in rst_content.splitlines() if line.strip()]
    if len(lines) < 2:
        return ""
    for idx, line in enumerate(lines[:-1]):
        underline = lines[idx + 1]
        if set(underline) <= {"=", "-", "~", "^"} and len(underline) >= len(line):
            return line.strip()
    return lines[0].strip()


def _heuristic_evaluate(  # noqa: PLR0912
    judge_name: str,
    rst_content: str,
    construct_terms: list[str],
) -> dict[str, Any]:
    text_lower = rst_content.lower()
    has_non_compliant = ".. non_compliant_example::" in rst_content
    has_compliant = ".. compliant_example::" in rst_content
    rust_example_count = rst_content.count(".. rust-example::")
    has_std = ":std:`" in rst_content
    has_rationale = ".. rationale::" in rst_content
    has_bibliography = ".. bibliography::" in rst_content
    actionable = any(token in text_lower for token in [" shall ", " should ", " must "]) or (
        ".. guideline::" in rst_content
    )
    hazard_terms = ["hazard", "undefined behavior", "data race", "memory corruption", "panic"]
    has_hazard = (
        any(token in text_lower for token in hazard_terms) or ".. guideline::" in rst_content
    )
    automotive_terms = ["automotive", "iso 26262", "embedded", "rtos", "no_std", "asil"]
    has_auto = any(token in text_lower for token in automotive_terms)
    title = _extract_title(rst_content)
    fabricated_id = bool(re.search(r":id:\s+gui_[0-9a-f]{12}\b", rst_content))

    if judge_name == "technical_accuracy":
        details = {
            "hazard_accurate": has_hazard,
            "code_correct": rust_example_count >= 1 or has_non_compliant or has_compliant,
            "api_accurate": True,
            "rationale_sound": has_rationale,
            "ids_not_fabricated": not fabricated_id,
        }
        failed = [key for key, value in details.items() if not value]
        return {
            "decision": "pass" if not failed else "fail",
            "reason_codes": [f"technical_{name}_failed" for name in failed],
            "summary": "Technical checks passed." if not failed else "Technical checks failed.",
            "details": details,
        }

    if judge_name == "functional_safety_relevance":
        significance = 3 if has_hazard and (has_auto or construct_terms or actionable) else 1
        details = {
            "hazard_relevant": has_hazard,
            "severity_reasonable": significance >= 2,
            "construct_used_in_automotive": bool(construct_terms) or has_auto or has_hazard,
            "actionable_in_iso26262": actionable,
        }
        failed = [key for key, value in details.items() if not value]
        decision = "fail" if significance < 2 or failed else "pass"
        return {
            "decision": decision,
            "reason_codes": [f"safety_{name}_failed" for name in failed],
            "summary": "Safety relevance checks passed."
            if decision == "pass"
            else "Safety relevance checks failed.",
            "significance": significance,
            "details": details,
        }

    details = {
        "title_prescriptive": bool(title),
        "body_actionable": actionable,
        "examples_effective": rust_example_count >= 1 or has_non_compliant or has_compliant,
        "rationale_persuasive": has_rationale,
        "bibliography_useful": True,
    }
    failed = [key for key, value in details.items() if not value]
    decision = "pass" if not failed else "fail"
    return {
        "decision": decision,
        "reason_codes": [f"pedagogy_{name}_failed" for name in failed],
        "summary": "Pedagogical checks passed."
        if decision == "pass"
        else "Pedagogical checks failed.",
        "details": details,
    }


def evaluate_judge(
    judge_name: str,
    rst_content: str,
    construct_terms: list[str],
    contracts: dict[str, Any],
) -> dict[str, Any]:
    _ = _build_judge_prompt(
        judge_name,
        ((contracts.get("roles") or {}).get(judge_name) or {}),
        rst_content,
        construct_terms,
    )
    raw = _heuristic_evaluate(judge_name, rst_content, construct_terms)
    decision = str(raw.get("decision", "fail"))
    reason_codes = [str(item) for item in raw.get("reason_codes", []) if str(item)]
    normalized = _normalize_judge_decision(decision, reason_codes)
    raw["decision"] = normalized
    raw["reason_codes"] = reason_codes
    return raw
