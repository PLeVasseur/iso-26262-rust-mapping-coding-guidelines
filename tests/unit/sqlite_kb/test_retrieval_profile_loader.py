from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.core.profile_loader import enforce_profile_corpus, load_retrieval_profile  # noqa: E402


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

    def test_load_retrieval_profile_rejects_ignored_query_mode_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = Path(temp_dir) / "bad_profile.yaml"
            profile_path.write_text(
                "profile: bad\ncorpus: fls_spec\nquery_mode_default: hybrid\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "query_mode_default"):
                load_retrieval_profile(profile_path)

    def test_fls_profile_no_longer_declares_ignored_query_mode_default(self) -> None:
        profile = load_retrieval_profile(
            ROOT / "config" / "retrieval_profiles" / "fls_spec_control.yaml"
        )
        self.assertNotIn("query_mode_default", profile)


if __name__ == "__main__":
    unittest.main()
