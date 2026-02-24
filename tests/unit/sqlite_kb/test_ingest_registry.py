from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.ingest.registry import list_ingest_strategies, resolve_ingest_strategy  # noqa: E402


class IngestRegistryTests(unittest.TestCase):
    def test_lists_expected_strategies(self) -> None:
        strategies = list_ingest_strategies()
        self.assertIn("rust_md_v1", strategies)
        self.assertIn("core_docs_rustdoc_v1", strategies)

    def test_legacy_core_docs_strategy_id_hard_fails_with_migration_hint(self) -> None:
        with self.assertRaises(RuntimeError) as context:
            resolve_ingest_strategy("core_docs_pdf_v1")
        self.assertIn("core_docs_rustdoc_v1", str(context.exception))

    def test_resolve_unknown_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            resolve_ingest_strategy("unknown")


if __name__ == "__main__":
    unittest.main()
