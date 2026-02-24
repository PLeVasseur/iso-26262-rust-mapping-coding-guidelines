from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class QueryPolicyPlacementBoundariesTests(unittest.TestCase):
    def test_shared_query_module_does_not_embed_target_aliases(self) -> None:
        query_module = ROOT / "scripts" / "retrieval" / "operations" / "query.py"
        text = query_module.read_text(encoding="utf-8")

        deny_tokens = (
            "qnx800",
            "qnx710",
            "wrs-vxworks",
            "thumbv7em",
            "TARGET_HINT_ALIASES",
            "TARGET_HINT_MATCHERS",
        )
        for token in deny_tokens:
            self.assertNotIn(
                token, text, msg=f"target policy leaked into shared query module: {token}"
            )


if __name__ == "__main__":
    unittest.main()
