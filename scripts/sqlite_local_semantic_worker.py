#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _append_jsonl(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
    except OSError:
        return


def _parse_int_header(raw: str | None, default: int = 0) -> int:
    try:
        return int(str(raw or default).strip() or default)
    except ValueError:
        return int(default)


def _parse_float_header(raw: str | None, default: float = 0.0) -> float:
    try:
        return float(str(raw or default).strip() or default)
    except ValueError:
        return float(default)


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _parse_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    raw_length = str(handler.headers.get("Content-Length", "0"))
    try:
        length = max(0, int(raw_length))
    except ValueError as exc:
        raise RuntimeError(f"Invalid Content-Length: {raw_length}") from exc

    body = handler.rfile.read(length).decode("utf-8") if length else "{}"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON payload: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("JSON payload must be an object")
    return payload


class _SemanticModelRuntime:
    def __init__(
        self,
        *,
        mode: str,
        model_id: str,
        cache_dir: Path,
        max_length: int,
        normalize_embeddings: bool,
        request_span_log_path: Path | None,
        service_role: str,
        device: str,
    ) -> None:
        self.mode = mode
        self.model_id = model_id
        self.cache_dir = cache_dir
        self.max_length = max(8, int(max_length))
        self.normalize_embeddings = bool(normalize_embeddings)
        self.request_span_log_path = request_span_log_path
        self.service_role = str(service_role).strip() or mode
        self.requested_device = str(device).strip().lower() or "auto"

        self._lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._torch: Any | None = None
        self._device: Any | None = None
        self._device_label: str = ""
        self._dtype_label: str = ""

    def log_request_span(self, payload: dict[str, Any]) -> None:
        entry = dict(payload)
        entry.setdefault("timestamp_utc", _utc_now())
        entry.setdefault("service_role", self.service_role)
        with self._log_lock:
            _append_jsonl(self.request_span_log_path, entry)

    def _effective_max_length(self) -> int:
        tokenizer_limit = getattr(self._tokenizer, "model_max_length", None)
        if isinstance(tokenizer_limit, int) and 8 <= tokenizer_limit <= 32768:
            return min(int(self.max_length), int(tokenizer_limit))
        return int(self.max_length)

    def _resolve_device(self, torch: Any) -> Any:
        choice = str(self.requested_device).strip().lower()
        if choice not in {"auto", "cpu", "mps", "cuda"}:
            raise RuntimeError(f"Unsupported device selector: {self.requested_device}")
        if choice == "cpu":
            return torch.device("cpu")
        if choice == "cuda":
            if not bool(torch.cuda.is_available()):
                raise RuntimeError("Requested device 'cuda' is unavailable")
            return torch.device("cuda")
        if choice == "mps":
            if not bool(torch.backends.mps.is_built()) or not bool(
                torch.backends.mps.is_available()
            ):
                raise RuntimeError("Requested device 'mps' is unavailable")
            return torch.device("mps")

        if bool(torch.cuda.is_available()):
            return torch.device("cuda")
        if bool(torch.backends.mps.is_built()) and bool(torch.backends.mps.is_available()):
            return torch.device("mps")
        return torch.device("cpu")

    def load(self) -> None:
        try:
            import torch
            from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Local Python backend requires optional dependencies. "
                "Install with: uv sync --extra semantic-local"
            ) from exc

        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._torch = torch
        self._device = self._resolve_device(torch)
        self._device_label = str(getattr(self._device, "type", self._device)).strip().lower()
        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_id,
            cache_dir=str(self.cache_dir),
            trust_remote_code=True,
        )

        if self.mode == "embeddings":
            self._model = AutoModel.from_pretrained(
                self.model_id,
                cache_dir=str(self.cache_dir),
                trust_remote_code=True,
            )
        else:
            self._model = AutoModelForSequenceClassification.from_pretrained(
                self.model_id,
                cache_dir=str(self.cache_dir),
                trust_remote_code=True,
            )

        model = self._model
        if model is None:
            raise RuntimeError(f"Failed to load model for mode={self.mode}")
        model.eval()
        model.to(self._device)

        dtype = ""
        parameters = getattr(model, "parameters", None)
        if callable(parameters):
            params = parameters()
            first_param: Any | None = None
            try:
                first_param = next(params)  # type: ignore[arg-type]
            except (StopIteration, TypeError):
                first_param = None
            if first_param is not None:
                dtype = str(getattr(first_param, "dtype", ""))
        self._dtype_label = dtype

    def embed(self, texts: list[str]) -> tuple[list[list[float]], dict[str, Any]]:
        if self._tokenizer is None or self._model is None or self._torch is None:
            raise RuntimeError("Embedding runtime not loaded")

        total_started = time.perf_counter()
        lock_wait_started = time.perf_counter()
        self._lock.acquire()
        lock_wait_ms = (time.perf_counter() - lock_wait_started) * 1000.0
        try:
            tokenize_started = time.perf_counter()
            encoded = self._tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=self._effective_max_length(),
                return_tensors="pt",
            )
            encoded = {name: tensor.to(self._device) for name, tensor in encoded.items()}
            tokenize_ms = (time.perf_counter() - tokenize_started) * 1000.0
            input_ids = encoded.get("input_ids")
            effective_seq_len = (
                int(input_ids.shape[1])
                if input_ids is not None and hasattr(input_ids, "shape")
                else 0
            )

            model_started = time.perf_counter()
            with self._torch.no_grad():
                output = self._model(**encoded)
                hidden = getattr(output, "last_hidden_state", None)
                if hidden is None:
                    raise RuntimeError("Embedding model output does not contain last_hidden_state")
            model_forward_ms = (time.perf_counter() - model_started) * 1000.0

            postprocess_started = time.perf_counter()
            with self._torch.no_grad():
                attention_mask = encoded.get("attention_mask")
                if attention_mask is None:
                    raise RuntimeError("Tokenizer output missing attention_mask")
                seq_len = int(hidden.size(1))
                masked = attention_mask[:, :seq_len]
                expanded = masked.unsqueeze(-1).expand(hidden.size()).float()
                pooled = (hidden * expanded).sum(dim=1) / expanded.sum(dim=1).clamp(min=1e-9)
                if self.normalize_embeddings:
                    pooled = self._torch.nn.functional.normalize(pooled, p=2, dim=1)

            vectors = [[float(value) for value in row] for row in pooled.cpu().tolist()]
            postprocess_ms = (time.perf_counter() - postprocess_started) * 1000.0
            total_ms = (time.perf_counter() - total_started) * 1000.0
            metrics = {
                "operation": "embed",
                "doc_count": int(len(texts)),
                "effective_seq_len": int(effective_seq_len),
                "device": str(self._device_label),
                "dtype": str(self._dtype_label),
                "queue_wait_ms": 0.0,
                "lock_wait_ms": round(float(lock_wait_ms), 3),
                "tokenize_ms": round(float(tokenize_ms), 3),
                "model_forward_ms": round(float(model_forward_ms), 3),
                "postprocess_ms": round(float(postprocess_ms), 3),
                "serialize_ms": 0.0,
                "total_ms": round(float(total_ms), 3),
            }
            return vectors, metrics
        finally:
            self._lock.release()

    def rerank(self, query_text: str, documents: list[str]) -> tuple[list[float], dict[str, Any]]:
        if self._tokenizer is None or self._model is None or self._torch is None:
            raise RuntimeError("Reranker runtime not loaded")

        pairs = [(query_text, document) for document in documents]
        total_started = time.perf_counter()
        lock_wait_started = time.perf_counter()
        self._lock.acquire()
        lock_wait_ms = (time.perf_counter() - lock_wait_started) * 1000.0
        try:
            tokenize_started = time.perf_counter()
            encoded = self._tokenizer(
                pairs,
                padding=True,
                truncation=True,
                max_length=self._effective_max_length(),
                return_tensors="pt",
            )
            encoded = {name: tensor.to(self._device) for name, tensor in encoded.items()}
            tokenize_ms = (time.perf_counter() - tokenize_started) * 1000.0
            input_ids = encoded.get("input_ids")
            effective_seq_len = (
                int(input_ids.shape[1])
                if input_ids is not None and hasattr(input_ids, "shape")
                else 0
            )

            model_started = time.perf_counter()
            with self._torch.no_grad():
                logits = self._model(**encoded).logits
            model_forward_ms = (time.perf_counter() - model_started) * 1000.0

            postprocess_started = time.perf_counter()
            if logits.ndim == 2:
                if logits.shape[1] == 1:
                    values = logits.squeeze(1)
                else:
                    values = logits[:, 0]
            else:
                values = logits
            scores = [float(value) for value in values.cpu().tolist()]
            postprocess_ms = (time.perf_counter() - postprocess_started) * 1000.0
            total_ms = (time.perf_counter() - total_started) * 1000.0
            metrics = {
                "operation": "rerank",
                "doc_count": int(len(documents)),
                "effective_seq_len": int(effective_seq_len),
                "device": str(self._device_label),
                "dtype": str(self._dtype_label),
                "queue_wait_ms": 0.0,
                "lock_wait_ms": round(float(lock_wait_ms), 3),
                "tokenize_ms": round(float(tokenize_ms), 3),
                "model_forward_ms": round(float(model_forward_ms), 3),
                "postprocess_ms": round(float(postprocess_ms), 3),
                "serialize_ms": 0.0,
                "total_ms": round(float(total_ms), 3),
            }
            return scores, metrics
        finally:
            self._lock.release()


class _Handler(BaseHTTPRequestHandler):
    runtime: _SemanticModelRuntime

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "" or self.path == "/health":
            _json_response(
                self,
                200,
                {
                    "status": "ok",
                    "mode": self.runtime.mode,
                    "model_id": self.runtime.model_id,
                    "device": str(self.runtime._device_label),
                    "dtype": str(self.runtime._dtype_label),
                },
            )
            return
        _json_response(self, 404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        request_started = time.perf_counter()
        header_trace_id = str(self.headers.get("X-Trace-Id", "")).strip()
        trace_id = header_trace_id or f"worker-{time.time_ns()}"

        def _base_span_payload() -> dict[str, Any]:
            return {
                "trace_id": trace_id,
                "request_id": trace_id,
                "run_id": str(self.headers.get("X-Run-Id", "")).strip(),
                "cell_id": str(self.headers.get("X-Cell-Id", "")).strip(),
                "prompt_id": str(self.headers.get("X-Prompt-Id", "")).strip(),
                "mode": str(self.headers.get("X-Mode", "")).strip(),
                "operation": str(self.headers.get("X-Operation", "")).strip(),
                "endpoint": str(self.path),
                "attempt_index": _parse_int_header(self.headers.get("X-Attempt-Index"), 0),
                "max_attempts": _parse_int_header(self.headers.get("X-Max-Attempts"), 0),
                "timeout_sec": _parse_float_header(self.headers.get("X-Timeout-Sec"), 0.0),
            }

        def _log_failure(error_class: str, error_detail: str, operation: str = "") -> None:
            payload = _base_span_payload()
            if operation and not payload.get("operation"):
                payload["operation"] = operation
            payload.update(
                {
                    "status": "fail",
                    "error_class": error_class,
                    "error_detail": error_detail,
                    "doc_count": 0,
                    "effective_seq_len": 0,
                    "queue_wait_ms": 0.0,
                    "lock_wait_ms": 0.0,
                    "tokenize_ms": 0.0,
                    "model_forward_ms": 0.0,
                    "postprocess_ms": 0.0,
                    "serialize_ms": 0.0,
                    "total_ms": round(float((time.perf_counter() - request_started) * 1000.0), 3),
                }
            )
            self.runtime.log_request_span(payload)

        try:
            payload = _parse_json_body(self)
            if self.path == "/v1/embeddings":
                if self.runtime.mode != "embeddings":
                    _log_failure(
                        "endpoint_unavailable", "embed endpoint disabled", operation="embed"
                    )
                    _json_response(self, 404, {"error": "endpoint_unavailable"})
                    return

                raw_input = payload.get("input")
                if isinstance(raw_input, str):
                    texts = [raw_input]
                elif isinstance(raw_input, list):
                    texts = [str(value) for value in raw_input]
                else:
                    raise RuntimeError("embeddings payload requires `input` string or list")

                vectors, metrics = self.runtime.embed(texts)
                _json_response(
                    self,
                    200,
                    {
                        "object": "list",
                        "model": self.runtime.model_id,
                        "data": [
                            {
                                "index": idx,
                                "object": "embedding",
                                "embedding": vector,
                            }
                            for idx, vector in enumerate(vectors)
                        ],
                    },
                )
                span_payload = _base_span_payload()
                if not span_payload.get("operation"):
                    span_payload["operation"] = "embed"
                span_payload.update(metrics)
                span_payload["status"] = "pass"
                span_payload["error_class"] = ""
                span_payload["error_detail"] = ""
                self.runtime.log_request_span(span_payload)
                return

            if self.path == "/v1/rerank":
                if self.runtime.mode != "rerank":
                    _log_failure(
                        "endpoint_unavailable", "rerank endpoint disabled", operation="rerank"
                    )
                    _json_response(self, 404, {"error": "endpoint_unavailable"})
                    return

                query_text = str(payload.get("query", "")).strip()
                if not query_text:
                    raise RuntimeError("rerank payload requires non-empty `query`")

                documents_raw = payload.get("documents")
                if not isinstance(documents_raw, list):
                    raise RuntimeError("rerank payload requires `documents` list")
                documents = [str(value) for value in documents_raw]
                scores, metrics = self.runtime.rerank(query_text, documents)
                _json_response(
                    self,
                    200,
                    {
                        "model": self.runtime.model_id,
                        "results": [
                            {
                                "index": idx,
                                "relevance_score": float(score),
                            }
                            for idx, score in enumerate(scores)
                        ],
                    },
                )
                span_payload = _base_span_payload()
                if not span_payload.get("operation"):
                    span_payload["operation"] = "rerank"
                span_payload.update(metrics)
                span_payload["status"] = "pass"
                span_payload["error_class"] = ""
                span_payload["error_detail"] = ""
                self.runtime.log_request_span(span_payload)
                return

            _log_failure("http_404", "unknown endpoint")
            _json_response(self, 404, {"error": "not_found"})
        except RuntimeError as exc:
            _log_failure("payload", str(exc))
            _json_response(self, 400, {"error": str(exc)})
        except OSError as exc:
            _log_failure("connection", str(exc))
            _json_response(self, 500, {"error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        _ = format
        _ = args


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local semantic worker over loopback HTTP")
    parser.add_argument("--mode", choices=("embeddings", "rerank"), required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument(
        "--cache-dir",
        default=".cache/sqlite_kb/models/hf",
    )
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "mps", "cuda"),
        default="auto",
        help="Device selector (auto resolves cuda -> mps -> cpu)",
    )
    parser.add_argument(
        "--normalize-embeddings",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="L2-normalize embedding vectors",
    )
    parser.add_argument(
        "--request-span-log-path",
        default="",
        help="Optional JSONL path for worker request span telemetry",
    )
    parser.add_argument(
        "--service-role",
        default="",
        help="Optional service role label for request spans",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime = _SemanticModelRuntime(
        mode=str(args.mode),
        model_id=str(args.model_id),
        cache_dir=Path(str(args.cache_dir)).resolve(),
        max_length=int(args.max_length),
        normalize_embeddings=bool(args.normalize_embeddings),
        request_span_log_path=(
            Path(str(args.request_span_log_path)).resolve()
            if str(args.request_span_log_path).strip()
            else None
        ),
        service_role=str(args.service_role),
        device=str(args.device),
    )

    try:
        runtime.load()
    except RuntimeError as exc:
        print(f"[local-semantic-worker][error] {exc}")
        return EXIT_RUNTIME_FAIL

    handler_cls = type("LocalSemanticWorkerHandler", (_Handler,), {})
    handler_cls.runtime = runtime
    server = ThreadingHTTPServer((str(args.host), int(args.port)), handler_cls)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
