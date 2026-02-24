from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class NoEnvRuntimeDefaultsTests(unittest.TestCase):
    def test_orchestration_paths_avoid_os_environ_defaults(self) -> None:
        targets = [
            ROOT / "scripts/sqlite_kb.py",
            ROOT / "scripts/sqlite_query_rust_reference.py",
            ROOT / "scripts/sqlite_eval_rust_reference_retrieval.py",
            ROOT / "scripts/sqlite_ci_retrieval_semantic.py",
            ROOT / "scripts/sqlite_ci_retrieval_pr_fast.py",
            ROOT / "scripts/sqlite_ci_retrieval_nightly_full.py",
            ROOT / "scripts/sqlite_materialize_rust_reference_embeddings.py",
            ROOT / "scripts/sqlite_capture_rust_reference_query_reviews.py",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("os.environ", text, msg=f"env default usage found in {path}")


if __name__ == "__main__":
    unittest.main()
