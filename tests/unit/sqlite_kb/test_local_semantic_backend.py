from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
THIS_DIR = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from sqlite_local_semantic_backend import (  # noqa: E402
    _python_worker_command,
    _require_loopback_url,
)


class LocalSemanticBackendTests(unittest.TestCase):
    def test_require_loopback_url_accepts_localhost(self) -> None:
        host, port = _require_loopback_url("http://127.0.0.1:8080")
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 8080)

    def test_require_loopback_url_rejects_non_loopback(self) -> None:
        with self.assertRaises(RuntimeError):
            _require_loopback_url("https://example.com:443")

    def test_python_worker_command_targets_mode_and_model(self) -> None:
        command = _python_worker_command(
            worker_script=ROOT / "scripts/sqlite_local_semantic_worker.py",
            mode="embeddings",
            host="127.0.0.1",
            port=8080,
            model_id="Qwen/Qwen3-Embedding-4B",
            cache_dir=ROOT / ".cache/sqlite_kb/models/hf",
            service_role="embed",
            request_span_log_path=None,
            device="mps",
        )

        self.assertEqual(command[0], sys.executable)
        self.assertIn("--mode", command)
        self.assertIn("embeddings", command)
        self.assertIn("Qwen/Qwen3-Embedding-4B", command)
        self.assertIn("--device", command)
        self.assertIn("mps", command)


if __name__ == "__main__":
    unittest.main()
