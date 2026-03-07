from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import sqlite_kb as _sqlite_kb  # noqa: E402

sqlite_kb = cast(Any, _sqlite_kb)


class SqliteKbCliTests(unittest.TestCase):
    def _parse_fails(self, argv: list[str]) -> None:
        with patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as exc:
                sqlite_kb.parse_args()
        self.assertEqual(exc.exception.code, 2)

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

    def test_parse_args_for_coding_guidelines_publish_from_run(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "sqlite_kb.py",
                "coding-guidelines",
                "publish-from-run",
                "--run-dir",
                ".cache/sqlite_kb/reports/demo",
                "--mode",
                "publishable",
            ],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.command_family, "coding-guidelines")
        self.assertEqual(args.coding_guidelines_subcommand, "publish-from-run")
        self.assertEqual(args.run_dir, ".cache/sqlite_kb/reports/demo")
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

    def test_parse_args_for_writer_targets(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "sqlite_kb.py",
                "writer-targets",
                "--profile",
                "fast",
            ],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.subcommand, "writer-targets")
        self.assertEqual(args.profile, "fast")

    def test_parse_args_for_writer_quality_gate(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "sqlite_kb.py",
                "writer-quality-gate",
                "--run-dir",
                ".cache/sqlite_kb/reports/demo",
            ],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.subcommand, "writer-quality-gate")
        self.assertEqual(args.run_dir, ".cache/sqlite_kb/reports/demo")

    def test_parse_args_for_writer_conformance(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "sqlite_kb.py",
                "writer-conformance",
                "--run-dir",
                ".cache/sqlite_kb/reports/demo",
            ],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.subcommand, "writer-conformance")
        self.assertEqual(args.run_dir, ".cache/sqlite_kb/reports/demo")

    def test_parse_args_for_writer_evidence(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "sqlite_kb.py",
                "writer-evidence",
                "--corpora",
                "rust_reference,core_docs",
                "--targets-manifest",
                ".cache/sqlite_kb/reports/demo/targets.json",
            ],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.subcommand, "writer-evidence")
        self.assertEqual(args.corpora, "rust_reference,core_docs")
        self.assertEqual(args.targets_manifest, ".cache/sqlite_kb/reports/demo/targets.json")

    def test_parse_args_for_writer_run_with_manifest(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "sqlite_kb.py",
                "writer-run",
                "--evidence-manifest",
                ".cache/sqlite_kb/reports/demo/manifest.json",
            ],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.subcommand, "writer-run")
        self.assertEqual(args.evidence_manifest, ".cache/sqlite_kb/reports/demo/manifest.json")

    def test_parse_args_for_writer_run_target_subset(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "sqlite_kb.py",
                "writer-run",
                "--evidence-manifest",
                ".cache/sqlite_kb/reports/demo/manifest.json",
                "--target-id",
                "RET-ISSUE-006",
                "--target-id",
                "RET-ISSUE-003",
            ],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.target_ids, ["RET-ISSUE-006", "RET-ISSUE-003"])

    def test_parse_args_for_writer_publish(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "sqlite_kb.py",
                "writer-publish",
                "--run-dir",
                ".cache/sqlite_kb/reports/demo",
            ],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.subcommand, "writer-publish")
        self.assertEqual(args.run_dir, ".cache/sqlite_kb/reports/demo")
        self.assertFalse(args.keep_worktree)

    def test_parse_args_for_writer_publish_keep_worktree(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "sqlite_kb.py",
                "writer-publish",
                "--run-dir",
                ".cache/sqlite_kb/reports/demo",
                "--keep-worktree",
            ],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.subcommand, "writer-publish")
        self.assertTrue(args.keep_worktree)

    def test_parse_args_for_writer_publish_review_audit(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "sqlite_kb.py",
                "writer-publish",
                "--run-dir",
                ".cache/sqlite_kb/reports/demo",
                "--mode",
                "review",
                "--audit-only",
            ],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.subcommand, "writer-publish")
        self.assertEqual(args.mode, "review")
        self.assertTrue(args.audit_only)

    def test_parse_args_for_writer_review_packet(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "sqlite_kb.py",
                "writer-review-packet",
                "--run-dir",
                ".cache/sqlite_kb/reports/demo",
            ],
        ):
            args = sqlite_kb.parse_args()
        self.assertEqual(args.subcommand, "writer-review-packet")
        self.assertEqual(args.run_dir, ".cache/sqlite_kb/reports/demo")

    def test_parse_args_rejects_corpus_for_writer_quality_gate(self) -> None:
        self._parse_fails(
            [
                "sqlite_kb.py",
                "writer-quality-gate",
                "--corpus",
                "rust_reference",
                "--run-dir",
                ".cache/sqlite_kb/reports/demo",
            ]
        )

    def test_parse_args_rejects_corpus_for_writer_review_packet(self) -> None:
        self._parse_fails(
            [
                "sqlite_kb.py",
                "writer-review-packet",
                "--corpus",
                "rust_reference",
                "--run-dir",
                ".cache/sqlite_kb/reports/demo",
            ]
        )

    def test_parse_args_rejects_corpus_for_writer_conformance(self) -> None:
        self._parse_fails(
            [
                "sqlite_kb.py",
                "writer-conformance",
                "--corpus",
                "rust_reference",
                "--run-dir",
                ".cache/sqlite_kb/reports/demo",
            ]
        )

    def test_parse_args_rejects_corpus_for_writer_publish(self) -> None:
        self._parse_fails(
            [
                "sqlite_kb.py",
                "writer-publish",
                "--corpus",
                "rust_reference",
                "--run-dir",
                ".cache/sqlite_kb/reports/demo",
            ]
        )

    def test_parse_args_rejects_corpus_for_writer_targets(self) -> None:
        self._parse_fails(
            [
                "sqlite_kb.py",
                "writer-targets",
                "--corpus",
                "rust_reference",
                "--profile",
                "fast",
            ]
        )

    def test_parse_args_rejects_corpus_for_writer_run(self) -> None:
        self._parse_fails(
            [
                "sqlite_kb.py",
                "writer-run",
                "--corpus",
                "rust_reference",
                "--evidence-manifest",
                ".cache/sqlite_kb/reports/demo/manifest.json",
            ]
        )

    def test_parse_args_rejects_writer_campaign(self) -> None:
        self._parse_fails(
            [
                "sqlite_kb.py",
                "writer-campaign",
                "--targets-manifest",
                ".cache/sqlite_kb/reports/demo/targets.json",
            ]
        )

    def test_artifact_commands_do_not_load_corpus_defaults(self) -> None:
        args = argparse.Namespace(
            subcommand="writer-quality-gate",
            run_dir=".cache/sqlite_kb/reports/demo",
            output="",
            extra_args=[],
        )
        with (
            patch.object(sqlite_kb, "parse_args", return_value=args),
            patch.object(
                sqlite_kb.writer_quality_gate_service, "run", return_value=17
            ) as service_run,
            patch.object(sqlite_kb, "load_corpus_runtime_defaults") as load_defaults,
            patch.object(sqlite_kb, "enforce_provenance_guard") as provenance_guard,
        ):
            status = sqlite_kb.main()
        self.assertEqual(status, 17)
        service_run.assert_called_once()
        load_defaults.assert_not_called()
        provenance_guard.assert_not_called()

    def test_writer_run_does_not_load_corpus_defaults(self) -> None:
        args = argparse.Namespace(
            subcommand="writer-run",
            evidence_manifest=".cache/sqlite_kb/reports/demo/manifest.json",
            run_id="",
            report_root="",
            contract_path="config/s0/writer_prompt_contracts.yaml",
            max_retries=1,
            model="",
            agent="",
            dry_run=True,
            extra_args=[],
        )
        with (
            patch.object(sqlite_kb, "parse_args", return_value=args),
            patch.object(sqlite_kb.writer_run_service, "run", return_value=23) as service_run,
            patch.object(sqlite_kb, "load_corpus_runtime_defaults") as load_defaults,
            patch.object(sqlite_kb, "enforce_provenance_guard") as provenance_guard,
        ):
            status = sqlite_kb.main()
        self.assertEqual(status, 23)
        service_run.assert_called_once()
        load_defaults.assert_not_called()
        provenance_guard.assert_not_called()

    def test_writer_evidence_checks_each_corpus_before_dispatch(self) -> None:
        defaults = argparse.Namespace(
            corpus="rust_reference",
            db_path=Path("/tmp/rust_reference.sqlite"),
            profile_name="default",
            eval_policy_path=Path("/tmp/eval_policy.yaml"),
            ingest_strategy="rust_md_v1",
            chunk_target_min_tokens=150,
            chunk_target_max_tokens=500,
            chunk_overlap_percent=0.0,
            supports_query=True,
        )
        core_defaults = argparse.Namespace(
            corpus="core_docs",
            db_path=Path("/tmp/core_docs.sqlite"),
            profile_name="default",
            eval_policy_path=Path("/tmp/eval_policy.yaml"),
            ingest_strategy="rust_md_v1",
            chunk_target_min_tokens=150,
            chunk_target_max_tokens=500,
            chunk_overlap_percent=0.0,
            supports_query=True,
        )
        args = argparse.Namespace(
            subcommand="writer-evidence",
            corpora="rust_reference,core_docs",
            profile_path="",
            targets_manifest=".cache/sqlite_kb/reports/demo/targets.json",
            run_id="",
            report_root="",
            output="",
            modes="lexical,semantic,hybrid",
            top_k=20,
            top_n=12,
            rrf_k=60,
            rank_window=100,
            allow_degraded=False,
            extra_args=[],
        )
        with (
            patch.object(sqlite_kb, "parse_args", return_value=args),
            patch.object(
                sqlite_kb, "load_corpus_runtime_defaults", side_effect=[defaults, core_defaults]
            ) as load_defaults,
            patch.object(sqlite_kb, "enforce_provenance_guard") as provenance_guard,
            patch.object(sqlite_kb.writer_evidence_service, "run", return_value=0) as service_run,
        ):
            status = sqlite_kb.main()
        self.assertEqual(status, 0)
        self.assertEqual(load_defaults.call_count, 2)
        self.assertEqual(provenance_guard.call_count, 2)
        service_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
