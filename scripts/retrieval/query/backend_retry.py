from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from retrieval.query.errors import ModeExecutionError
from semantic_backend_client import SemanticBackendError


def classify_semantic_error(detail: str) -> str:
    text = str(detail).strip().lower()
    if "timed out" in text:
        return "timeout"
    if "http 404" in text:
        return "http_404"
    if "http " in text:
        return "http"
    if "non-json" in text or "payload" in text:
        return "payload"
    if "request failed" in text:
        return "connection"
    return "unknown"


def with_semantic_retries(
    description: str,
    retries: int,
    call: Callable[[], Any],
    telemetry: list[dict[str, Any]] | None = None,
) -> Any:
    attempts = max(0, int(retries)) + 1
    last_error: SemanticBackendError | None = None
    attempt_events: list[dict[str, Any]] = []
    total_started = time.perf_counter()
    for attempt in range(attempts):
        attempt_started = time.perf_counter()
        try:
            value = call()
            attempt_duration_ms = (time.perf_counter() - attempt_started) * 1000.0
            attempt_events.append(
                {
                    "attempt": attempt + 1,
                    "status": "pass",
                    "duration_ms": round(float(attempt_duration_ms), 3),
                }
            )
            if telemetry is not None:
                telemetry.append(
                    {
                        "operation": description,
                        "status": "pass",
                        "max_attempts": attempts,
                        "attempts_used": attempt + 1,
                        "retry_count": attempt,
                        "total_duration_ms": round(
                            float((time.perf_counter() - total_started) * 1000.0),
                            3,
                        ),
                        "attempt_events": attempt_events,
                    }
                )
            return value
        except SemanticBackendError as exc:
            last_error = exc
            attempt_duration_ms = (time.perf_counter() - attempt_started) * 1000.0
            detail = str(exc)
            attempt_events.append(
                {
                    "attempt": attempt + 1,
                    "status": "fail",
                    "duration_ms": round(float(attempt_duration_ms), 3),
                    "error": detail,
                    "error_class": classify_semantic_error(detail),
                }
            )
            if attempt + 1 >= attempts:
                break
            time.sleep(0.2 * (attempt + 1))

    message = str(last_error) if last_error is not None else "unknown semantic backend error"
    if telemetry is not None:
        telemetry.append(
            {
                "operation": description,
                "status": "fail",
                "max_attempts": attempts,
                "attempts_used": attempts,
                "retry_count": max(0, attempts - 1),
                "error": message,
                "error_class": classify_semantic_error(message),
                "total_duration_ms": round(
                    float((time.perf_counter() - total_started) * 1000.0), 3
                ),
                "attempt_events": attempt_events,
            }
        )
    raise ModeExecutionError(
        code="SEMANTIC_BACKEND_UNAVAILABLE",
        message=f"{description} failed after {attempts} attempts: {message}",
    )
