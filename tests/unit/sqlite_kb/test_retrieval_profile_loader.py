from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.core.profile_loader import enforce_profile_corpus  # noqa: E402


class RetrievalProfileLoaderTests(unittest.TestCase):
    def test_enforce_profile_corpus_allows_empty_profile_corpus(self) -> None:
        self.assertEqual(
            enforce_profile_corpus("rust_reference", {"profile_name": "x"}),
            "rust_reference",
        )

    def test_enforce_profile_corpus_accepts_matching_corpus(self) -> None:
        self.assertEqual(
            enforce_profile_corpus("core_docs", {"corpus": "core_docs"}),
            "core_docs",
        )

    def test_enforce_profile_corpus_rejects_mismatch(self) -> None:
        with self.assertRaises(RuntimeError):
            enforce_profile_corpus("rust_reference", {"corpus": "core_docs"})


if __name__ == "__main__":
    unittest.main()
