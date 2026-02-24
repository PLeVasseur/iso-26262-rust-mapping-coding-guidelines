from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.core.engine import build_runtime_config  # noqa: E402
from retrieval.core.fusion import apply_rrf_hybrid_scores  # noqa: E402
from retrieval.core.rewrite import rewrite_query_text  # noqa: E402


class RetrievalCoreModuleTests(unittest.TestCase):
    def test_runtime_config_validates_fusion_choice(self) -> None:
        with self.assertRaises(ValueError):
            build_runtime_config(
                top_k=10,
                candidate_limit=100,
                hybrid_fusion_method="unknown",
            )

    def test_runtime_config_clamps_window_and_limits(self) -> None:
        config = build_runtime_config(top_k=5, candidate_limit=2, hybrid_rrf_window=0)
        self.assertEqual(config.top_k, 5)
        self.assertEqual(config.candidate_limit, 5)
        self.assertGreaterEqual(config.hybrid_rrf_window, 40)

    def test_rrf_fusion_scores_merge_sources(self) -> None:
        lexical_rows = [{"statement_id": "s1"}, {"statement_id": "s2"}]
        semantic_rows = [
            {"statement_id": "s2", "reranker_score": 0.9, "semantic_score": 0.8},
            {"statement_id": "s1", "reranker_score": 0.2, "semantic_score": 0.4},
        ]
        merged = {"s1": {"statement_id": "s1"}, "s2": {"statement_id": "s2"}}

        rows, debug = apply_rrf_hybrid_scores(
            merged_rows=merged,
            lexical_rows=lexical_rows,
            semantic_rows=semantic_rows,
            rrf_k=60,
            rrf_window=10,
            row_identity=lambda row: str(row.get("statement_id", "")),
        )

        self.assertEqual(len(rows), 2)
        self.assertIn("contribution_counts", debug)
        self.assertTrue(all("rrf_score" in row for row in rows))

    def test_rewrite_query_text_expands_terms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.yaml"
            rules_path.write_text(
                "\n".join(
                    [
                        "version: 1",
                        "strategy: unit-test",
                        "token_expansions:",
                        "  error: [result]",
                        "row_marker_terms:",
                        "  1d: [defensive]",
                        "mode_terms:",
                        "  semantic: [intent]",
                    ]
                ),
                encoding="utf-8",
            )
            payload = rewrite_query_text(
                query_text="error handling",
                row_marker="1d",
                mode="semantic",
                rewrite_mode="auto",
                rewrite_rules_path=rules_path,
            )
            rewritten = str(payload.get("rewritten_query", ""))
            self.assertIn("result", rewritten)
            self.assertIn("defensive", rewritten)
            self.assertIn("intent", rewritten)


if __name__ == "__main__":
    unittest.main()
