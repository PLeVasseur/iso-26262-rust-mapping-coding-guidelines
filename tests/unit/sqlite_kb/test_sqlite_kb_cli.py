from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import sqlite_kb  # noqa: E402


class SqliteKbCliTests(unittest.TestCase):
    def test_parse_args_for_query(self) -> None:
        with patch.object(sys, "argv", ["sqlite_kb.py", "query", "--corpus", "rust_reference"]):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.subcommand, "query")
        self.assertEqual(args.corpus, "rust_reference")

    def test_parse_args_for_eval_with_extras(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "sqlite_kb.py",
                "eval",
                "--corpus",
                "core_docs",
                "--",
                "--top-k",
                "5",
            ],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.subcommand, "eval")
        self.assertEqual(args.corpus, "core_docs")
        self.assertIn("--top-k", args.extra_args)


if __name__ == "__main__":
    unittest.main()
