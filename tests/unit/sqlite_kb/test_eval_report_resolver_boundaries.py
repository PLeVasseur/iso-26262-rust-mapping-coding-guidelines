from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class EvalReportResolverBoundaryTests(unittest.TestCase):
    def test_shared_human_report_module_has_no_corpus_specific_table_names(self) -> None:
        shared = ROOT / "scripts" / "retrieval" / "eval" / "human_report.py"
        text = shared.read_text(encoding="utf-8")

        forbidden = (
            "core_docs_chunk_metadata",
            "target_triple",
            "doc_path",
            "rust_reference",
            "core_docs",
        )
        for token in forbidden:
            self.assertNotIn(token, text, msg=f"corpus detail leaked into shared report: {token}")

    def test_corpus_specific_fields_live_in_resolvers(self) -> None:
        core_resolver = (
            ROOT / "scripts" / "retrieval" / "eval" / "human_report_resolvers" / "core_docs.py"
        )
        rust_resolver = (
            ROOT / "scripts" / "retrieval" / "eval" / "human_report_resolvers" / "rust_reference.py"
        )

        core_text = core_resolver.read_text(encoding="utf-8")
        rust_text = rust_resolver.read_text(encoding="utf-8")

        self.assertIn("core_docs_chunk_metadata", core_text)
        self.assertIn("target_triple", core_text)
        self.assertIn("doc_path", rust_text)


if __name__ == "__main__":
    unittest.main()
