from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.corpora.runtime_paths import resolve_corpus_runtime_paths  # noqa: E402


class RetrievalRuntimePathsTests(unittest.TestCase):
    def test_defaults_resolve_by_corpus(self) -> None:
        runtime = resolve_corpus_runtime_paths(
            root=ROOT,
            corpus="core_docs",
            db_path="",
            contract_path="",
            query_log_root="",
            rewrite_rules_path="",
        )
        self.assertEqual(runtime.corpus, "core_docs")
        self.assertEqual(
            runtime.db_path, (ROOT / ".cache/sqlite_kb/current/core_docs.sqlite").resolve()
        )
        self.assertEqual(
            runtime.contract_path,
            (ROOT / "config/sqlite_query_contracts/core_docs.yaml").resolve(),
        )
        self.assertEqual(
            runtime.query_log_root,
            (ROOT / ".cache/sqlite_kb/query_logs/core_docs").resolve(),
        )
        self.assertEqual(
            runtime.rewrite_rules_path,
            (ROOT / "config/sqlite_query_rewrite/core_docs_rewrite.yaml").resolve(),
        )

    def test_explicit_overrides_win(self) -> None:
        runtime = resolve_corpus_runtime_paths(
            root=ROOT,
            corpus="rust_reference",
            db_path="tmp/custom.sqlite",
            contract_path="tmp/custom_contract.yaml",
            query_log_root="tmp/logs",
            rewrite_rules_path="tmp/rewrite.yaml",
        )
        self.assertEqual(runtime.db_path, (ROOT / "tmp/custom.sqlite").resolve())
        self.assertEqual(runtime.contract_path, (ROOT / "tmp/custom_contract.yaml").resolve())
        self.assertEqual(runtime.query_log_root, (ROOT / "tmp/logs").resolve())
        self.assertEqual(runtime.rewrite_rules_path, (ROOT / "tmp/rewrite.yaml").resolve())


if __name__ == "__main__":
    unittest.main()
