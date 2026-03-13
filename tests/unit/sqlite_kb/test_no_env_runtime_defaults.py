from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class NoEnvRuntimeDefaultsTests(unittest.TestCase):
    def test_orchestration_paths_avoid_os_environ_defaults(self) -> None:
        targets = [
            ROOT / "scripts/sqlite_kb.py",
            ROOT / "scripts/retrieval/operations/query.py",
            ROOT / "scripts/retrieval/operations/eval.py",
            ROOT / "scripts/sqlite_ci_retrieval_semantic.py",
            ROOT / "scripts/sqlite_ci_retrieval_pr_fast.py",
            ROOT / "scripts/sqlite_ci_retrieval_nightly_full.py",
            ROOT / "scripts/retrieval/operations/materialize.py",
            ROOT / "scripts/retrieval/operations/capture.py",
        ]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("os.environ", text, msg=f"env default usage found in {path}")


if __name__ == "__main__":
    unittest.main()
