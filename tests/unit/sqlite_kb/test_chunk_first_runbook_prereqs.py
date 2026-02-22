from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from sqlite_build_rust_reference import RETRIEVAL_CORPUS_VALUES, parse_args  # noqa: E402


class ChunkFirstRunbookPrereqsTests(unittest.TestCase):
    def test_retrieval_corpus_flag_accepts_statement_and_chunk(self) -> None:
        for corpus in ("statement", "chunk"):
            with patch.object(
                sys,
                "argv",
                ["sqlite_build_rust_reference.py", "--retrieval-corpus", corpus],
            ):
                args = parse_args()
                self.assertEqual(args.retrieval_corpus, corpus)

        self.assertEqual(set(RETRIEVAL_CORPUS_VALUES), {"statement", "chunk"})

    def test_chunk_contract_has_required_query_ids(self) -> None:
        contract_path = ROOT / "config" / "sqlite_query_contracts" / "rust_reference_chunk.yaml"
        self.assertTrue(contract_path.exists())

        payload = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
        query_ids = set((payload.get("queries") or {}).keys())
        required = {
            "chunk_corpus_v1_all",
            "lexical_chunk_search_v1",
            "table1_row_requirements_v2",
            "snapshot_metadata",
            "semantic_model_metadata",
        }
        self.assertEqual(required - query_ids, set())


if __name__ == "__main__":
    unittest.main()
