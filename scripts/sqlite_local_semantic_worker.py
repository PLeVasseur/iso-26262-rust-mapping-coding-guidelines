#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


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
    ) -> None:
        self.mode = mode
        self.model_id = model_id
        self.cache_dir = cache_dir
        self.max_length = max(8, int(max_length))
        self.normalize_embeddings = bool(normalize_embeddings)

        self._lock = threading.Lock()
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._torch: Any | None = None
        self._device: Any | None = None

    def _effective_max_length(self) -> int:
        tokenizer_limit = getattr(self._tokenizer, "model_max_length", None)
        if isinstance(tokenizer_limit, int) and 8 <= tokenizer_limit <= 32768:
            return min(int(self.max_length), int(tokenizer_limit))
        return int(self.max_length)

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
        self._device = torch.device("cpu")
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

        self._model.eval()
        self._model.to(self._device)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._tokenizer is None or self._model is None or self._torch is None:
            raise RuntimeError("Embedding runtime not loaded")

        with self._lock:
            encoded = self._tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=self._effective_max_length(),
                return_tensors="pt",
            )
            encoded = {name: tensor.to(self._device) for name, tensor in encoded.items()}

            with self._torch.no_grad():
                output = self._model(**encoded)
                hidden = getattr(output, "last_hidden_state", None)
                if hidden is None:
                    raise RuntimeError("Embedding model output does not contain last_hidden_state")

                attention_mask = encoded.get("attention_mask")
                if attention_mask is None:
                    raise RuntimeError("Tokenizer output missing attention_mask")
                seq_len = int(hidden.size(1))
                masked = attention_mask[:, :seq_len]
                expanded = masked.unsqueeze(-1).expand(hidden.size()).float()
                pooled = (hidden * expanded).sum(dim=1) / expanded.sum(dim=1).clamp(min=1e-9)
                if self.normalize_embeddings:
                    pooled = self._torch.nn.functional.normalize(pooled, p=2, dim=1)

            return [[float(value) for value in row] for row in pooled.cpu().tolist()]

    def rerank(self, query_text: str, documents: list[str]) -> list[float]:
        if self._tokenizer is None or self._model is None or self._torch is None:
            raise RuntimeError("Reranker runtime not loaded")

        pairs = [(query_text, document) for document in documents]
        with self._lock:
            encoded = self._tokenizer(
                pairs,
                padding=True,
                truncation=True,
                max_length=self._effective_max_length(),
                return_tensors="pt",
            )
            encoded = {name: tensor.to(self._device) for name, tensor in encoded.items()}

            with self._torch.no_grad():
                logits = self._model(**encoded).logits

            if logits.ndim == 2:
                if logits.shape[1] == 1:
                    values = logits.squeeze(1)
                else:
                    values = logits[:, 0]
            else:
                values = logits
            return [float(value) for value in values.cpu().tolist()]


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
                },
            )
            return
        _json_response(self, 404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = _parse_json_body(self)
            if self.path == "/v1/embeddings":
                if self.runtime.mode != "embeddings":
                    _json_response(self, 404, {"error": "endpoint_unavailable"})
                    return

                raw_input = payload.get("input")
                if isinstance(raw_input, str):
                    texts = [raw_input]
                elif isinstance(raw_input, list):
                    texts = [str(value) for value in raw_input]
                else:
                    raise RuntimeError("embeddings payload requires `input` string or list")

                vectors = self.runtime.embed(texts)
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
                return

            if self.path == "/v1/rerank":
                if self.runtime.mode != "rerank":
                    _json_response(self, 404, {"error": "endpoint_unavailable"})
                    return

                query_text = str(payload.get("query", "")).strip()
                if not query_text:
                    raise RuntimeError("rerank payload requires non-empty `query`")

                documents_raw = payload.get("documents")
                if not isinstance(documents_raw, list):
                    raise RuntimeError("rerank payload requires `documents` list")
                documents = [str(value) for value in documents_raw]
                scores = self.runtime.rerank(query_text, documents)
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
                return

            _json_response(self, 404, {"error": "not_found"})
        except RuntimeError as exc:
            _json_response(self, 400, {"error": str(exc)})
        except OSError as exc:
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
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument(
        "--normalize-embeddings",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="L2-normalize embedding vectors",
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
