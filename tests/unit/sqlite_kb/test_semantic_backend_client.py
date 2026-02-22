from __future__ import annotations

import json
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
THIS_DIR = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from semantic_backend_client import (  # noqa: E402
    SemanticBackendConfig,
    check_semantic_backend,
)


class _MockSemanticHandler(BaseHTTPRequestHandler):
    def _write_json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/health", "/"}:
            self._write_json({"status": "ok"})
            return
        self._write_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        payload = json.loads(body)

        if self.path == "/v1/embeddings":
            inputs = payload.get("input") or []
            rows = [{"embedding": [0.1, 0.2, 0.3]} for _ in inputs]
            self._write_json({"data": rows})
            return

        if self.path == "/v1/rerank":
            documents = payload.get("documents") or []
            rows = [
                {"index": idx, "relevance_score": 1.0 - (idx * 0.01)}
                for idx, _ in enumerate(documents)
            ]
            self._write_json({"results": rows})
            return

        self._write_json({"error": "not found"}, status=404)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        _ = format
        _ = args


class SemanticBackendClientTests(unittest.TestCase):
    def _start_server(
        self, handler: type[BaseHTTPRequestHandler]
    ) -> tuple[HTTPServer, threading.Thread, str]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            host, port = sock.getsockname()

        server = HTTPServer((host, port), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://{host}:{port}"
        return server, thread, base_url

    def _start_mock_server(self) -> tuple[HTTPServer, threading.Thread, str]:
        return self._start_server(_MockSemanticHandler)

    def test_check_semantic_backend_passes_with_mock_server(self) -> None:
        server, thread, base_url = self._start_mock_server()
        try:
            result = check_semantic_backend(
                SemanticBackendConfig(
                    base_url=base_url,
                    embed_model_id="Qwen/Qwen3-Embedding-4B",
                    reranker_model_id="BAAI/bge-reranker-v2-m3",
                    timeout_sec=1.0,
                )
            )
        finally:
            server.shutdown()
            thread.join(timeout=2.0)
            server.server_close()

        self.assertTrue(result["ok"])
        self.assertEqual(result["checks"][0]["name"], "health_embed")

    def test_check_semantic_backend_passes_with_split_endpoints(self) -> None:
        class _EmbedOnlyHandler(BaseHTTPRequestHandler):
            def _write_json(self, payload: dict[str, object], status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if self.path in {"/health", "/"}:
                    self._write_json({"status": "ok"})
                    return
                self._write_json({"error": "not found"}, status=404)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                payload = json.loads(body)
                if self.path == "/v1/embeddings":
                    inputs = payload.get("input") or []
                    self._write_json({"data": [{"embedding": [0.1, 0.2]} for _ in inputs]})
                    return
                self._write_json({"error": "not found"}, status=404)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                _ = format
                _ = args

        class _RerankOnlyHandler(BaseHTTPRequestHandler):
            def _write_json(self, payload: dict[str, object], status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if self.path in {"/health", "/"}:
                    self._write_json({"status": "ok"})
                    return
                self._write_json({"error": "not found"}, status=404)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                payload = json.loads(body)
                if self.path == "/v1/rerank":
                    documents = payload.get("documents") or []
                    rows = [
                        {"index": idx, "relevance_score": 1.0 - (idx * 0.01)}
                        for idx, _ in enumerate(documents)
                    ]
                    self._write_json({"results": rows})
                    return
                self._write_json({"error": "not found"}, status=404)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                _ = format
                _ = args

        embed_server, embed_thread, embed_url = self._start_server(_EmbedOnlyHandler)
        rerank_server, rerank_thread, rerank_url = self._start_server(_RerankOnlyHandler)
        try:
            result = check_semantic_backend(
                SemanticBackendConfig(
                    base_url=embed_url,
                    embed_base_url=embed_url,
                    rerank_base_url=rerank_url,
                    embed_model_id="Qwen/Qwen3-Embedding-4B",
                    reranker_model_id="BAAI/bge-reranker-v2-m3",
                    timeout_sec=1.0,
                )
            )
        finally:
            embed_server.shutdown()
            embed_thread.join(timeout=2.0)
            embed_server.server_close()
            rerank_server.shutdown()
            rerank_thread.join(timeout=2.0)
            rerank_server.server_close()

        self.assertTrue(result["ok"])
        self.assertEqual(result["embed_base_url"], embed_url)
        self.assertEqual(result["rerank_base_url"], rerank_url)

    def test_check_semantic_backend_fails_for_unreachable_endpoint(self) -> None:
        result = check_semantic_backend(
            SemanticBackendConfig(
                base_url="http://127.0.0.1:1",
                embed_model_id="Qwen/Qwen3-Embedding-4B",
                reranker_model_id="BAAI/bge-reranker-v2-m3",
                timeout_sec=0.2,
            )
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "SEMANTIC_BACKEND_UNAVAILABLE")

    def test_check_semantic_backend_handles_socket_oserror(self) -> None:
        config = SemanticBackendConfig(
            base_url="http://127.0.0.1:8080",
            embed_model_id="Qwen/Qwen3-Embedding-4B",
            reranker_model_id="BAAI/bge-reranker-v2-m3",
            timeout_sec=0.2,
        )
        with patch("semantic_backend_client.request.urlopen", side_effect=OSError("boom")):
            result = check_semantic_backend(config)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "SEMANTIC_BACKEND_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
