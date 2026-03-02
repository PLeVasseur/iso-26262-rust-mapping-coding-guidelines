"""External retry wrapper for OpenCode HTTP server API calls.

This module is the sole retry orchestrator for machine-to-machine LLM calls.
Do not place retry loops inside agent prompts.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

CONVENTION_RETRY_BUDGET = 50
COMPILATION_RETRY_BUDGET = 15
RESERVE_BUDGET = 5
BACKOFF_BASE = 2.0
MAX_BACKOFF = 120.0
TIMEOUT_SECONDS = 300
TRUNCATION_THRESHOLD = 0.30
DEFAULT_SERVER_URL = "http://localhost:4096"


@dataclass
class RetryResult:
    success: bool
    output: dict[str, Any] | None
    attempts: int
    violations_remaining: list[str]
    budget_exhausted: bool
    oscillation_detected: bool
    diminishing_returns: bool
    truncation_retries: int = 0


def is_likely_truncated(
    output: dict[str, Any],
    expected_field_count: int | None = None,
    expected_min_length: int | None = None,
) -> bool:
    """Detect likely truncated output from compaction or silent exits."""
    if expected_field_count is not None:
        actual_fields = sum(1 for value in output.values() if value not in (None, ""))
        if actual_fields < expected_field_count * TRUNCATION_THRESHOLD:
            return True

    if expected_min_length is not None:
        actual_length = len(json.dumps(output))
        if actual_length < expected_min_length * TRUNCATION_THRESHOLD:
            return True

    if output and all(value in (None, "", [], {}) for value in output.values()):
        return True

    return False


def create_session(
    *,
    title: str | None = None,
    server_url: str = DEFAULT_SERVER_URL,
    timeout: int = 30,
) -> str:
    """Create a new OpenCode session and return the session ID."""
    body: dict[str, Any] = {}
    if title:
        body["title"] = title

    request = urllib.request.Request(
        f"{server_url}/session",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    response = urllib.request.urlopen(request, timeout=timeout)
    payload = json.loads(response.read().decode())
    session_id = payload.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("session_create_missing_id")
    return session_id


def _extract_text_from_parts(parts: list[dict[str, Any]]) -> str:
    texts: list[str] = []
    for part in parts:
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
    return "\n".join(texts)


def _extract_json_from_text(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    fenced = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    brace_start = text.find("{")
    if brace_start >= 0:
        depth = 0
        for idx, char in enumerate(text[brace_start:], brace_start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[brace_start : idx + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    except (json.JSONDecodeError, ValueError):
                        break

    return None


def run_opencode(
    session_id: str,
    prompt: str,
    *,
    server_url: str = DEFAULT_SERVER_URL,
    timeout: int = TIMEOUT_SECONDS,
    model: str | None = None,
    agent: str | None = None,
) -> tuple[int, dict[str, Any] | None]:
    """Send a prompt to OpenCode via HTTP API.

    Returns (0, parsed_output) on success and (nonzero, None) on failures.
    """
    body: dict[str, Any] = {"parts": [{"type": "text", "text": prompt}]}
    if model:
        body["model"] = model
    if agent:
        body["agent"] = agent

    try:
        request = urllib.request.Request(
            f"{server_url}/session/{session_id}/message",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        response = urllib.request.urlopen(request, timeout=timeout)
        payload = json.loads(response.read().decode())
        parts = payload.get("parts", [])
        if not isinstance(parts, list):
            return 0, None
        text = _extract_text_from_parts(parts)
        if not text:
            return 0, None

        parsed = _extract_json_from_text(text)
        if parsed is not None:
            return 0, parsed

        return 0, {"raw_text": text, "parts": parts}
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError):
        return -1, None


def retry_with_violations(
    session_id: str,
    initial_prompt: str,
    parse_violations_fn: Callable[[dict[str, Any]], list[str] | None],
    build_retry_prompt_fn: Callable[[str, list[str]], str],
    *,
    budget: int = CONVENTION_RETRY_BUDGET,
    expected_field_count: int | None = None,
    expected_min_length: int | None = None,
) -> RetryResult:
    """Run retry loop with content validation and violation feedback."""
    violation_history: list[set[str]] = []
    excluded_violations: set[str] = set()
    prompt = initial_prompt
    attempts = 0
    truncation_retries = 0
    output: dict[str, Any] | None = None
    active_violations: list[str] = []
    current_session_id = session_id

    for attempt in range(budget):
        attempts += 1
        exit_code, output = run_opencode(current_session_id, prompt)

        if exit_code != 0:
            time.sleep(min(BACKOFF_BASE**attempt, MAX_BACKOFF))
            continue

        if output is None:
            time.sleep(BACKOFF_BASE)
            continue

        if is_likely_truncated(output, expected_field_count, expected_min_length):
            truncation_retries += 1
            if truncation_retries <= 1:
                current_session_id = create_session(title=f"{session_id}-trunc-retry")
                continue

        violations = parse_violations_fn(output)
        if violations is None:
            continue

        active_violations = [value for value in violations if value not in excluded_violations]
        if not active_violations:
            return RetryResult(
                success=True,
                output=output,
                attempts=attempts,
                violations_remaining=list(excluded_violations),
                budget_exhausted=False,
                oscillation_detected=bool(excluded_violations),
                diminishing_returns=False,
                truncation_retries=truncation_retries,
            )

        current_set = set(active_violations)
        violation_history.append(current_set)

        if len(violation_history) >= 2:
            prev = violation_history[-2]
            historical_union = set().union(*violation_history[:-1])
            for value in current_set:
                if value not in prev and value in historical_union:
                    excluded_violations.add(value)

        if len(violation_history) >= 4:
            recent_counts = [
                len(history - excluded_violations) for history in violation_history[-3:]
            ]
            if recent_counts[-1] >= recent_counts[0]:
                return RetryResult(
                    success=False,
                    output=output,
                    attempts=attempts,
                    violations_remaining=active_violations,
                    budget_exhausted=False,
                    oscillation_detected=bool(excluded_violations),
                    diminishing_returns=True,
                    truncation_retries=truncation_retries,
                )

        prompt = build_retry_prompt_fn(initial_prompt, active_violations)

    return RetryResult(
        success=False,
        output=output,
        attempts=attempts,
        violations_remaining=active_violations,
        budget_exhausted=True,
        oscillation_detected=bool(excluded_violations),
        diminishing_returns=False,
        truncation_retries=truncation_retries,
    )
