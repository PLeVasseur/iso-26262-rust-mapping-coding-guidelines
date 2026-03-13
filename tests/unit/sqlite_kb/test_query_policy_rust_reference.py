from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.query_policies.rust_reference import apply_intent_path_preference  # noqa: E402


class RustReferenceQueryPolicyTests(unittest.TestCase):
    def test_style_queries_prioritize_diagnostics_paths(self) -> None:
        rows = [
            {"doc_path": "items/external-blocks.md", "section_heading": "ABI"},
            {"doc_path": "attributes/diagnostics.md", "section_heading": "Lint check attributes"},
            {"doc_path": "patterns.md", "section_heading": "Patterns"},
        ]
        reordered = apply_intent_path_preference(
            rows,
            query_text="How do style and lint conventions affect analyzability in audits?",
        )
        self.assertEqual(str(reordered[0].get("doc_path", "")), "attributes/diagnostics.md")

    def test_style_queries_prefer_lint_headings_over_generic_diagnostics(self) -> None:
        rows = [
            {
                "doc_path": "attributes/diagnostics.md",
                "section_heading": "The `must_use` attribute",
            },
            {
                "doc_path": "attributes/diagnostics.md",
                "section_heading": "Lint check attributes",
            },
        ]
        reordered = apply_intent_path_preference(
            rows,
            query_text="Which style and lint controls improve analyzability in audits?",
        )
        self.assertEqual(
            str(reordered[0].get("section_heading", "")),
            "Lint check attributes",
        )

    def test_trait_queries_prioritize_trait_paths(self) -> None:
        rows = [
            {"doc_path": "unsafety.md", "section_heading": "Unsafety"},
            {"doc_path": "items/traits.md", "section_heading": "Traits"},
            {"doc_path": "types/trait-object.md", "section_heading": "Trait objects"},
        ]
        reordered = apply_intent_path_preference(
            rows,
            query_text="Which trait and abstraction choices support architecture integrity?",
        )
        self.assertIn(
            str(reordered[0].get("doc_path", "")),
            {"items/traits.md", "types/trait-object.md"},
        )

    def test_control_flow_queries_demote_generic_block_expression_paths(self) -> None:
        rows = [
            {
                "doc_path": "expressions/block-expr.md",
                "section_heading": "Block expressions",
            },
            {
                "doc_path": "expressions/match-expr.md",
                "section_heading": "`match` expressions",
            },
            {
                "doc_path": "attributes/type_system.md",
                "section_heading": "The `non_exhaustive` attribute",
            },
        ]
        reordered = apply_intent_path_preference(
            rows,
            query_text=(
                "Where can non-exhaustive match handling create ambiguous control-flow behavior?"
            ),
        )
        self.assertNotEqual(str(reordered[0].get("doc_path", "")), "expressions/block-expr.md")
        self.assertIn(
            str(reordered[0].get("doc_path", "")),
            {"expressions/match-expr.md", "attributes/type_system.md"},
        )


if __name__ == "__main__":
    unittest.main()
