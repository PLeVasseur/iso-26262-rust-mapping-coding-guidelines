from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.corpora.config_loader import load_corpus_runtime_defaults  # noqa: E402


class CorpusConfigLoaderTests(unittest.TestCase):
    def test_rust_reference_defaults_load(self) -> None:
        cfg = load_corpus_runtime_defaults(root=ROOT, corpus="rust_reference")
        self.assertEqual(cfg.corpus, "rust_reference")
        self.assertEqual(cfg.profile_name, "rust_reference_control")
        self.assertTrue(cfg.supports_query)
        self.assertTrue(cfg.supports_eval)
        self.assertEqual(cfg.ingest_strategy, "rust_md_v1")
        self.assertEqual(cfg.chunk_target_min_tokens, 150)
        self.assertEqual(cfg.chunk_target_max_tokens, 500)
        self.assertEqual(cfg.chunk_overlap_percent, 0.0)
        self.assertTrue(str(cfg.db_path).endswith("rust_reference.sqlite"))

    def test_core_docs_defaults_load(self) -> None:
        cfg = load_corpus_runtime_defaults(root=ROOT, corpus="core_docs")
        self.assertEqual(cfg.corpus, "core_docs")
        self.assertEqual(cfg.profile_name, "core_docs_control")
        self.assertTrue(str(cfg.contract_path).endswith("core_docs.yaml"))
        self.assertEqual(cfg.ingest_strategy, "core_docs_rustdoc_v1")
        self.assertEqual(cfg.chunk_overlap_percent, 0.0)
        self.assertTrue(cfg.supports_eval)
        self.assertTrue(cfg.supports_migrate)


if __name__ == "__main__":
    unittest.main()
