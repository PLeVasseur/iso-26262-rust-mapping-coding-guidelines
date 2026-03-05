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
    def test_import_bootstrap_adds_repo_root(self) -> None:
        self.assertIn(str(ROOT), sys.path)

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

    def test_parse_args_for_guidelines_repo_autopilot(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "sqlite_kb.py",
                "guidelines-repo",
                "autopilot",
                "--profile",
                "fast",
                "--mode",
                "publishable",
            ],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.command_family, "guidelines-repo")
        self.assertEqual(args.guidelines_subcommand, "autopilot")
        self.assertEqual(args.profile, "fast")
        self.assertEqual(args.mode, "publishable")

    def test_parse_args_for_guidelines_repo_doctor(self) -> None:
        with patch.object(
            sys,
            "argv",
            ["sqlite_kb.py", "guidelines-repo", "doctor", "--mode", "exploratory"],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.command_family, "guidelines-repo")
        self.assertEqual(args.guidelines_subcommand, "doctor")
        self.assertEqual(args.mode, "exploratory")

    def test_parse_args_for_guidelines_repo_bootstrap_verify(self) -> None:
        with patch.object(
            sys,
            "argv",
            ["sqlite_kb.py", "guidelines-repo", "bootstrap-guidelines-repo", "--verify"],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.command_family, "guidelines-repo")
        self.assertEqual(args.guidelines_subcommand, "bootstrap-guidelines-repo")
        self.assertTrue(bool(args.verify))

    def test_parse_args_for_guidelines_repo_bump_pin(self) -> None:
        with patch.object(
            sys,
            "argv",
            ["sqlite_kb.py", "guidelines-repo", "bump-pin", "--revision", "abc123"],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.command_family, "guidelines-repo")
        self.assertEqual(args.guidelines_subcommand, "bump-pin")
        self.assertEqual(args.revision, "abc123")

    def test_parse_args_for_writer_host_run(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "sqlite_kb.py",
                "writer-host-run",
                "--corpus",
                "rust_reference",
                "--targets",
                "RET-ISSUE-005,RET-RESOLVE-008",
                "--query-mode",
                "lexical",
            ],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.subcommand, "writer-host-run")
        self.assertEqual(args.corpus, "rust_reference")
        self.assertEqual(args.targets, "RET-ISSUE-005,RET-RESOLVE-008")


if __name__ == "__main__":
    unittest.main()
