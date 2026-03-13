from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.query_policies.core_docs import apply_target_hint_preference  # noqa: E402


class TargetHintAliasRankingTests(unittest.TestCase):
    def test_qnx_hint_prioritizes_qnx_rows(self) -> None:
        rows = [
            {
                "source_anchor": "https://doc.rust-lang.org/core/?search=x&target=x86_64-unknown-linux-gnu"
            },
            {
                "source_anchor": "https://doc.rust-lang.org/core/?search=x&target=x86_64-pc-nto-qnx800"
            },
        ]
        ranked = apply_target_hint_preference(rows, query_text="Need QNX nto80 guidance")
        self.assertIn("qnx800", str(ranked[0].get("source_anchor", "")).lower())

    def test_no_target_hint_preserves_order(self) -> None:
        rows = [{"source_anchor": "a"}, {"source_anchor": "b"}]
        ranked = apply_target_hint_preference(rows, query_text="How does Option::ok_or_else work?")
        self.assertEqual([r["source_anchor"] for r in ranked], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
