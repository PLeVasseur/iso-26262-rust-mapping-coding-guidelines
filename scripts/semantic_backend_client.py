#!/usr/bin/env python3
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request


class SemanticBackendError(RuntimeError):
    """Raised when semantic backend calls fail."""


@dataclass(frozen=True)
class SemanticBackendConfig:
    base_url: str
    embed_model_id: str
    reranker_model_id: str
    timeout_sec: float = 10.0
    embed_base_url: str | None = None
    rerank_base_url: str | None = None
    attempt_log_path: str | None = None
    attempt_context: str = ""
    attempt_run_id: str = ""
    attempt_cell_id: str = ""
    attempt_prompt_id: str = ""
    attempt_mode: str = ""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _classify_attempt_error(detail: str) -> str:
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


def _append_attempt_log(config: SemanticBackendConfig, payload: dict[str, Any]) -> None:
    target = str(config.attempt_log_path or "").strip()
    if not target:
        return

    path = Path(target)
    entry = dict(payload)
    entry["timestamp_utc"] = _utc_now()
    entry["context"] = str(config.attempt_context).strip()

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            json.dump(entry, handle, sort_keys=True)
            handle.write("\n")
    except OSError:
        return


def _normalize_base_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise SemanticBackendError("Semantic backend URL must be non-empty")
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    return value.rstrip("/")


def resolve_embed_base_url(config: SemanticBackendConfig) -> str:
    candidate = str(config.embed_base_url or "").strip() or str(config.base_url)
    return _normalize_base_url(candidate)


def resolve_rerank_base_url(config: SemanticBackendConfig) -> str:
    candidate = str(config.rerank_base_url or "").strip() or str(config.base_url)
    return _normalize_base_url(candidate)


def _json_request(
    method: str,
    url: str,
    timeout_sec: float,
    payload: dict[str, Any] | None = None,
    request_headers: dict[str, str] | None = None,
) -> Any:
    data: bytes | None = None
    headers: dict[str, str] = dict(request_headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    req = request.Request(url=url, data=data, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout_sec) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise SemanticBackendError(f"HTTP {exc.code} for {url}: {message}") from exc
    except error.URLError as exc:
        raise SemanticBackendError(f"Request failed for {url}: {exc}") from exc
    except OSError as exc:
        raise SemanticBackendError(f"Request failed for {url}: {exc}") from exc

    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SemanticBackendError(f"Non-JSON response from {url}") from exc


def _build_attempt_headers(
    config: SemanticBackendConfig,
    *,
    operation: str,
    trace_id: str,
    attempt_index: int,
    max_attempts: int,
) -> dict[str, str]:
    headers: dict[str, str] = {
        "X-Trace-Id": trace_id,
        "X-Operation": operation,
        "X-Attempt-Index": str(int(attempt_index)),
        "X-Max-Attempts": str(int(max_attempts)),
        "X-Timeout-Sec": str(float(config.timeout_sec)),
        "X-Attempt-Context": str(config.attempt_context).strip(),
    }
    if str(config.attempt_run_id).strip():
        headers["X-Run-Id"] = str(config.attempt_run_id).strip()
    if str(config.attempt_cell_id).strip():
        headers["X-Cell-Id"] = str(config.attempt_cell_id).strip()
    if str(config.attempt_prompt_id).strip():
        headers["X-Prompt-Id"] = str(config.attempt_prompt_id).strip()
    if str(config.attempt_mode).strip():
        headers["X-Mode"] = str(config.attempt_mode).strip()
    return headers


def _extract_embeddings(payload: Any) -> list[list[float]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            vectors: list[list[float]] = []
            for row in payload["data"]:
                if isinstance(row, dict) and isinstance(row.get("embedding"), list):
                    vectors.append([float(value) for value in row["embedding"]])
            if vectors:
                return vectors
        if isinstance(payload.get("embeddings"), list):
            vectors = payload["embeddings"]
            if vectors and isinstance(vectors[0], list):
                return [[float(value) for value in row] for row in vectors]
        if isinstance(payload.get("embedding"), list):
            return [[float(value) for value in payload["embedding"]]]

    if isinstance(payload, list) and payload and isinstance(payload[0], list):
        return [[float(value) for value in row] for row in payload]

    raise SemanticBackendError("Embedding response payload missing vectors")


def _extract_reranker_scores(payload: Any, expected_count: int) -> list[float]:
    if expected_count <= 0:
        return []

    if isinstance(payload, dict):
        if isinstance(payload.get("results"), list):
            scores = [0.0] * expected_count
            for row in payload["results"]:
                if not isinstance(row, dict):
                    continue
                index = row.get("index")
                if isinstance(index, int) and 0 <= index < expected_count:
                    value = row.get("relevance_score", row.get("score", 0.0))
                    scores[index] = float(value)
            return scores

        if isinstance(payload.get("scores"), list):
            raw_scores = payload["scores"]
            if len(raw_scores) != expected_count:
                raise SemanticBackendError("Reranker score length mismatch")
            return [float(value) for value in raw_scores]

        if isinstance(payload.get("data"), list):
            raw_rows = payload["data"]
            scores = [0.0] * expected_count
            populated = 0
            for row in raw_rows:
                if not isinstance(row, dict):
                    continue
                index = row.get("index")
                if isinstance(index, int) and 0 <= index < expected_count:
                    scores[index] = float(row.get("score", row.get("relevance_score", 0.0)))
                    populated += 1
            if populated:
                return scores

    if isinstance(payload, list):
        if len(payload) != expected_count:
            raise SemanticBackendError("Reranker score length mismatch")
        if payload and isinstance(payload[0], dict):
            scores = [0.0] * expected_count
            for idx, row in enumerate(payload):
                scores[idx] = float(row.get("score", row.get("relevance_score", 0.0)))
            return scores
        return [float(value) for value in payload]

    raise SemanticBackendError("Reranker response payload missing scores")


def _try_embedding_request(config: SemanticBackendConfig, texts: list[str]) -> list[list[float]]:
    base = resolve_embed_base_url(config)
    variants = [
        (
            f"{base}/v1/embeddings",
            {"model": config.embed_model_id, "input": texts},
        ),
        (
            f"{base}/embed",
            {"model": config.embed_model_id, "inputs": texts},
        ),
        (
            f"{base}/embed",
            {"inputs": texts},
        ),
    ]
    errors: list[str] = []
    for attempt_index, (url, payload) in enumerate(variants, start=1):
        started = time.perf_counter()
        trace_id = uuid.uuid4().hex
        headers = _build_attempt_headers(
            config,
            operation="embed",
            trace_id=trace_id,
            attempt_index=attempt_index,
            max_attempts=len(variants),
        )
        try:
            response = _json_request(
                "POST",
                url=url,
                timeout_sec=config.timeout_sec,
                payload=payload,
                request_headers=headers,
            )
            vectors = _extract_embeddings(response)
            if len(vectors) != len(texts):
                raise SemanticBackendError(
                    f"Embedding count mismatch ({len(vectors)} != {len(texts)})"
                )
            _append_attempt_log(
                config,
                {
                    "operation": "embed",
                    "status": "pass",
                    "endpoint": url,
                    "attempt_index": attempt_index,
                    "max_attempts": len(variants),
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "timeout_sec": float(config.timeout_sec),
                    "input_count": len(texts),
                    "trace_id": trace_id,
                    "request_id": trace_id,
                    "run_id": str(config.attempt_run_id).strip(),
                    "cell_id": str(config.attempt_cell_id).strip(),
                    "prompt_id": str(config.attempt_prompt_id).strip(),
                    "mode": str(config.attempt_mode).strip(),
                },
            )
            return vectors
        except SemanticBackendError as exc:
            detail = str(exc)
            errors.append(detail)
            _append_attempt_log(
                config,
                {
                    "operation": "embed",
                    "status": "fail",
                    "endpoint": url,
                    "attempt_index": attempt_index,
                    "max_attempts": len(variants),
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "timeout_sec": float(config.timeout_sec),
                    "input_count": len(texts),
                    "error": detail,
                    "error_class": _classify_attempt_error(detail),
                    "trace_id": trace_id,
                    "request_id": trace_id,
                    "run_id": str(config.attempt_run_id).strip(),
                    "cell_id": str(config.attempt_cell_id).strip(),
                    "prompt_id": str(config.attempt_prompt_id).strip(),
                    "mode": str(config.attempt_mode).strip(),
                },
            )
    raise SemanticBackendError("Embedding request failed: " + " | ".join(errors))


def _try_rerank_request(
    config: SemanticBackendConfig,
    query_text: str,
    documents: list[str],
) -> list[float]:
    base = resolve_rerank_base_url(config)
    variants = [
        (
            f"{base}/v1/rerank",
            {
                "model": config.reranker_model_id,
                "query": query_text,
                "documents": documents,
            },
        ),
        (
            f"{base}/rerank",
            {
                "model": config.reranker_model_id,
                "query": query_text,
                "texts": documents,
            },
        ),
        (
            f"{base}/rerank",
            {
                "query": query_text,
                "documents": documents,
            },
        ),
    ]
    errors: list[str] = []
    for attempt_index, (url, payload) in enumerate(variants, start=1):
        started = time.perf_counter()
        trace_id = uuid.uuid4().hex
        headers = _build_attempt_headers(
            config,
            operation="rerank",
            trace_id=trace_id,
            attempt_index=attempt_index,
            max_attempts=len(variants),
        )
        try:
            response = _json_request(
                "POST",
                url=url,
                timeout_sec=config.timeout_sec,
                payload=payload,
                request_headers=headers,
            )
            scores = _extract_reranker_scores(response, expected_count=len(documents))
            _append_attempt_log(
                config,
                {
                    "operation": "rerank",
                    "status": "pass",
                    "endpoint": url,
                    "attempt_index": attempt_index,
                    "max_attempts": len(variants),
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "timeout_sec": float(config.timeout_sec),
                    "document_count": len(documents),
                    "score_count": len(scores),
                    "trace_id": trace_id,
                    "request_id": trace_id,
                    "run_id": str(config.attempt_run_id).strip(),
                    "cell_id": str(config.attempt_cell_id).strip(),
                    "prompt_id": str(config.attempt_prompt_id).strip(),
                    "mode": str(config.attempt_mode).strip(),
                },
            )
            return scores
        except SemanticBackendError as exc:
            detail = str(exc)
            errors.append(detail)
            _append_attempt_log(
                config,
                {
                    "operation": "rerank",
                    "status": "fail",
                    "endpoint": url,
                    "attempt_index": attempt_index,
                    "max_attempts": len(variants),
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "timeout_sec": float(config.timeout_sec),
                    "document_count": len(documents),
                    "error": detail,
                    "error_class": _classify_attempt_error(detail),
                    "trace_id": trace_id,
                    "request_id": trace_id,
                    "run_id": str(config.attempt_run_id).strip(),
                    "cell_id": str(config.attempt_cell_id).strip(),
                    "prompt_id": str(config.attempt_prompt_id).strip(),
                    "mode": str(config.attempt_mode).strip(),
                },
            )
    raise SemanticBackendError("Reranker request failed: " + " | ".join(errors))


def embed_texts(config: SemanticBackendConfig, texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return _try_embedding_request(config, texts)


def rerank_texts(
    config: SemanticBackendConfig,
    query_text: str,
    documents: list[str],
) -> list[float]:
    if not documents:
        return []
    return _try_rerank_request(config=config, query_text=query_text, documents=documents)


def check_semantic_backend(config: SemanticBackendConfig) -> dict[str, Any]:
    base = _normalize_base_url(config.base_url)
    embed_base = resolve_embed_base_url(config)
    rerank_base = resolve_rerank_base_url(config)
    checks: list[dict[str, Any]] = []

    def _health_check(name: str, target_base: str) -> bool:
        health_errors: list[str] = []
        for endpoint in ("/health", "/"):
            url = parse.urljoin(f"{target_base}/", endpoint.lstrip("/"))
            req = request.Request(url=url, method="GET")
            try:
                with request.urlopen(req, timeout=config.timeout_sec) as response:
                    status = int(getattr(response, "status", response.getcode()))
                if 200 <= status < 400:
                    checks.append(
                        {
                            "name": name,
                            "status": "pass",
                            "endpoint": endpoint,
                            "base_url": target_base,
                        }
                    )
                    return True
                health_errors.append(f"{url} returned unexpected status {status}")
            except error.HTTPError as exc:
                health_errors.append(f"HTTP {exc.code} for {url}")
            except error.URLError as exc:
                health_errors.append(f"Request failed for {url}: {exc}")
            except OSError as exc:
                health_errors.append(f"Request failed for {url}: {exc}")

        checks.append(
            {
                "name": name,
                "status": "fail",
                "base_url": target_base,
                "detail": " | ".join(health_errors),
            }
        )
        return False

    if not _health_check("health_embed", embed_base):
        checks.append(
            {"name": "health_rerank", "status": "skipped", "detail": "embed health failed"}
        )
        return {
            "ok": False,
            "base_url": base,
            "embed_base_url": embed_base,
            "rerank_base_url": rerank_base,
            "embed_model_id": config.embed_model_id,
            "reranker_model_id": config.reranker_model_id,
            "checks": checks,
            "error_code": "SEMANTIC_BACKEND_UNAVAILABLE",
        }

    if rerank_base == embed_base:
        checks.append(
            {
                "name": "health_rerank",
                "status": "pass",
                "endpoint": "shared",
                "base_url": rerank_base,
            }
        )
    elif not _health_check("health_rerank", rerank_base):
        return {
            "ok": False,
            "base_url": base,
            "embed_base_url": embed_base,
            "rerank_base_url": rerank_base,
            "embed_model_id": config.embed_model_id,
            "reranker_model_id": config.reranker_model_id,
            "checks": checks,
            "error_code": "SEMANTIC_BACKEND_UNAVAILABLE",
        }

    try:
        probe_vectors = embed_texts(config, ["semantic backend health probe"])
        checks.append(
            {
                "name": "embed",
                "status": "pass",
                "vector_dim": len(probe_vectors[0]) if probe_vectors else 0,
            }
        )
    except SemanticBackendError as exc:
        checks.append({"name": "embed", "status": "fail", "detail": str(exc)})
        return {
            "ok": False,
            "base_url": base,
            "embed_base_url": embed_base,
            "rerank_base_url": rerank_base,
            "embed_model_id": config.embed_model_id,
            "reranker_model_id": config.reranker_model_id,
            "checks": checks,
            "error_code": "SEMANTIC_BACKEND_UNAVAILABLE",
        }

    try:
        probe_scores = rerank_texts(
            config=config,
            query_text="semantic backend health probe",
            documents=["semantic backend health probe", "unrelated probe text"],
        )
        checks.append(
            {
                "name": "rerank",
                "status": "pass",
                "score_count": len(probe_scores),
            }
        )
    except SemanticBackendError as exc:
        checks.append({"name": "rerank", "status": "fail", "detail": str(exc)})
        return {
            "ok": False,
            "base_url": base,
            "embed_base_url": embed_base,
            "rerank_base_url": rerank_base,
            "embed_model_id": config.embed_model_id,
            "reranker_model_id": config.reranker_model_id,
            "checks": checks,
            "error_code": "SEMANTIC_BACKEND_UNAVAILABLE",
        }

    return {
        "ok": True,
        "base_url": base,
        "embed_base_url": embed_base,
        "rerank_base_url": rerank_base,
        "embed_model_id": config.embed_model_id,
        "reranker_model_id": config.reranker_model_id,
        "checks": checks,
    }
