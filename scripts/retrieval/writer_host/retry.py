from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from opencode_model_registry import ensure_model_available


@dataclass
class RetryOutcome:
    output: dict[str, Any]
    attempts: int
    violations: list[str]
    oscillation_detected: bool
    diminishing_returns: bool
    budget_exhausted: bool
    session_id: str
    failure_kind: str | None
    failure_detail: str


@dataclass
class OpencodeCallResult:
    exit_code: int
    output: dict[str, Any] | None
    failure_kind: str | None
    failure_detail: str


def _extract_text_lines(stdout: str) -> str:
    texts: list[str] = []
    for line in stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if str(parsed.get("type", "")) != "text":
            continue
        part_raw = parsed.get("part")
        part: dict[str, Any] = part_raw if isinstance(part_raw, dict) else {}
        text = str(part.get("text", ""))
        if text:
            texts.append(text)
    return "\n".join(texts).strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    candidate = str(text).strip()
    if candidate.startswith("```"):
        candidate = candidate.removeprefix("```json").removeprefix("```").strip()
        if candidate.endswith("```"):
            candidate = candidate[:-3].strip()
    if candidate.startswith("{") and candidate.endswith("}"):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(candidate[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return None


def _run_opencode_cli(*, prompt: str, model: str | None, agent: str | None) -> OpencodeCallResult:
    command = ["opencode", "run", "--format", "json"]
    if agent:
        command.extend(["--agent", str(agent)])
    if model:
        ensure_model_available(str(model))
        command.extend(["--model", str(model)])
    command.append(prompt)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
    except subprocess.TimeoutExpired:
        return OpencodeCallResult(
            exit_code=124,
            output=None,
            failure_kind="transport_timeout",
            failure_detail="opencode run timed out",
        )
    if int(result.returncode) != 0:
        stderr = (result.stderr or result.stdout).strip()
        failure_kind = "transport_failure"
        lowered = stderr.lower()
        if "model not found" in lowered or "providermodelnotfounderror" in lowered:
            failure_kind = "model_not_found"
        return OpencodeCallResult(
            exit_code=int(result.returncode),
            output=None,
            failure_kind=failure_kind,
            failure_detail=stderr,
        )
    text = _extract_text_lines(result.stdout)
    if not text:
        return OpencodeCallResult(
            exit_code=0,
            output=None,
            failure_kind="parse_failure",
            failure_detail="no text events produced by opencode run",
        )
    parsed = _extract_json_object(text)
    if parsed is None:
        return OpencodeCallResult(
            exit_code=0,
            output=None,
            failure_kind="parse_failure",
            failure_detail="no JSON object found in opencode text output",
        )
    return OpencodeCallResult(exit_code=0, output=parsed, failure_kind=None, failure_detail="")


def run_role_with_retry(
    *,
    role_name: str,
    prompt: str,
    validate_output: Callable[[dict[str, Any]], list[str]],
    max_retries: int,
    model: str | None,
    agent: str | None,
) -> RetryOutcome:
    max_attempts = max(1, int(max_retries) + 1)
    current_prompt = prompt
    previous: set[str] | None = None
    history_sizes: list[int] = []
    last_output: dict[str, Any] = {}
    last_violations: list[str] = []

    for attempt in range(1, max_attempts + 1):
        try:
            result = _run_opencode_cli(prompt=current_prompt, model=model, agent=agent)
        except Exception as exc:  # noqa: BLE001
            result = OpencodeCallResult(
                exit_code=1,
                output=None,
                failure_kind="model_not_found"
                if "configured OpenCode model not available" in str(exc)
                else "transport_failure",
                failure_detail=str(exc),
            )
        output = result.output
        if result.failure_kind is not None or output is None:
            last_output = {}
            last_violations = [str(result.failure_kind or "transport_failure")]
            time.sleep(min(2.0**attempt, 15.0))
            continue

        violations = list(validate_output(output))
        last_output = output
        last_violations = violations
        if not violations:
            return RetryOutcome(
                output=output,
                attempts=attempt,
                violations=[],
                oscillation_detected=False,
                diminishing_returns=False,
                budget_exhausted=False,
                session_id=f"writer-host-cli::{role_name}",
                failure_kind=None,
                failure_detail="",
            )

        current_set = set(violations)
        if previous is not None and current_set == previous:
            return RetryOutcome(
                output=output,
                attempts=attempt,
                violations=violations,
                oscillation_detected=True,
                diminishing_returns=False,
                budget_exhausted=False,
                session_id=f"writer-host-cli::{role_name}",
                failure_kind=None,
                failure_detail="",
            )
        previous = current_set
        history_sizes.append(len(violations))
        if len(history_sizes) >= 3 and history_sizes[-1] >= history_sizes[-3]:
            return RetryOutcome(
                output=output,
                attempts=attempt,
                violations=violations,
                oscillation_detected=False,
                diminishing_returns=True,
                budget_exhausted=False,
                session_id=f"writer-host-cli::{role_name}",
                failure_kind=None,
                failure_detail="",
            )

        violation_lines = "\n".join(f"- {item}" for item in violations)
        current_prompt = (
            f"{prompt}\n\n"
            "Your previous output violated constraints:\n"
            f"{violation_lines}\n\n"
            "Return corrected JSON only."
        )

    return RetryOutcome(
        output=last_output,
        attempts=max_attempts,
        violations=last_violations or ["budget_exhausted"],
        oscillation_detected=False,
        diminishing_returns=False,
        budget_exhausted=True,
        session_id=f"writer-host-cli::{role_name}",
        failure_kind=last_violations[0] if len(last_violations) == 1 else None,
        failure_detail="",
    )
