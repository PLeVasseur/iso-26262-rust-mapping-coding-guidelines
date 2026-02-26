from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.corpora.registry import get_corpus_adapter, list_supported_corpora  # noqa: E402


class RetrievalCorpusRegistryTests(unittest.TestCase):
    def test_registry_lists_supported_corpora(self) -> None:
        supported = list_supported_corpora()
        self.assertIn("rust_reference", supported)
        self.assertIn("core_docs", supported)
        self.assertIn("guidelines_repo", supported)

    def test_rust_reference_adapter_defaults(self) -> None:
        config = get_corpus_adapter("rust_reference").config
        self.assertEqual(config.corpus_name, "rust_reference")
        self.assertEqual(
            config.default_db_path, Path(".cache/sqlite_kb/current/rust_reference.sqlite")
        )
        self.assertEqual(
            config.default_contract_path,
            Path("config/sqlite_query_contracts/rust_reference_chunk.yaml"),
        )

    def test_core_docs_adapter_defaults(self) -> None:
        config = get_corpus_adapter("core_docs").config
        self.assertEqual(config.corpus_name, "core_docs")
        self.assertEqual(config.default_db_path, Path(".cache/sqlite_kb/current/core_docs.sqlite"))
        self.assertEqual(
            config.default_contract_path, Path("config/sqlite_query_contracts/core_docs.yaml")
        )

    def test_unsupported_corpus_raises(self) -> None:
        with self.assertRaises(ValueError):
            get_corpus_adapter("unknown")

    def test_guidelines_repo_adapter_defaults(self) -> None:
        config = get_corpus_adapter("guidelines_repo").config
        self.assertEqual(config.corpus_name, "guidelines_repo")
        self.assertFalse(config.supports_query)
        self.assertTrue(config.supports_build)
        self.assertTrue(config.supports_inspect)


if __name__ == "__main__":
    unittest.main()
