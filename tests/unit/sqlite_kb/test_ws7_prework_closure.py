from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.services.ws7_prework_closure import (  # noqa: E402
    generate_ws7_prework_closure_packet,
    maybe_refresh_ws7_prework_closure_packet,
    write_ws7_prework_closure_packet,
)


class Ws7PreworkClosureTests(unittest.TestCase):
    def _write_junit(self, root: Path, *, name: str, testcases: list[str]) -> Path:
        path = root / name
        body = "".join(
            f'<testcase classname="{classname}" name="case_{index}" time="0.001" />'
            for index, classname in enumerate(testcases, start=1)
        )
        path.write_text(
            (
                '<?xml version="1.0" encoding="utf-8"?>'
                f'<testsuites name="pytest tests"><testsuite name="pytest" errors="0" '
                f'failures="0" skipped="0" tests="{len(testcases)}" '
                'timestamp="2026-03-09T21:00:00+00:00">'
                f"{body}</testsuite></testsuites>\n"
            ),
            encoding="utf-8",
        )
        return path

    def _write_current_chunk_first_report(self, root: Path, *, corpus: str) -> Path:
        db_path = root / ".cache" / "sqlite_kb" / "current" / f"{corpus}.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_text(f"db for {corpus}\n", encoding="utf-8")
        target = (
            root
            / ".cache"
            / "sqlite_kb"
            / "reports"
            / corpus
            / "current_chunk_first_validation.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "corpus": corpus,
                    "db_path": str(db_path),
                    "db_sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
                    "passed": True,
                    "chunk_count": 1,
                    "chunks_fts_count": 1,
                    "schema_user_version": 7,
                    "latest_migration_id": "20260309_005_chunk_fts_rowids",
                    "latest_snapshot_id": f"{corpus}-snapshot-1",
                    "chunk_fts_mapping": {
                        "passed": True,
                        "applicable": True,
                        "chunk_count": 1,
                        "chunks_fts_count": 1,
                        "chunk_fts_rowids_count": 1,
                    },
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return target

    def test_write_ws7_prework_closure_packet_emits_report_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            artifact_a = temp_root / "proof_a.json"
            artifact_b = temp_root / "proof_b.jsonl"
            artifact_a.write_text('{"status": "pass"}\n', encoding="utf-8")
            artifact_b.write_text('{"status": "pass"}\n', encoding="utf-8")

            report_path, manifest_path = write_ws7_prework_closure_packet(
                report_dir=temp_root / "closure",
                priorities=[
                    {"priority": 1, "required_artifact_labels": ["proof-a"]},
                    {"priority": 2, "required_artifact_labels": ["proof-b"]},
                ],
                proof_artifacts=[
                    {"label": "proof-a", "path": artifact_a, "priority": 1},
                    {"label": "proof-b", "path": artifact_b, "priority": 2},
                ],
                corpora=["fls_spec", "core_docs", "rust_reference"],
                deferred_items=["WS7 staged runtime implementation"],
            )

            self.assertTrue(report_path.is_file())
            self.assertTrue(manifest_path.is_file())

            report = json.loads(report_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["priorities"][0]["status"], "pass")
            self.assertTrue(report["prework_only"])
            self.assertTrue(report["non_authoritative_for_full_ws7_runtime"])
            self.assertEqual(report["corpora"], ["core_docs", "fls_spec", "rust_reference"])
            self.assertEqual(len(report["proof_artifacts"]), 2)
            self.assertTrue(manifest["prework_only"])
            self.assertTrue(manifest["non_authoritative_for_full_ws7_runtime"])
            self.assertEqual(manifest["included_files"][0]["path"], str(report_path))

    def test_write_ws7_prework_closure_packet_fails_when_artifact_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            with self.assertRaisesRegex(RuntimeError, "WS7 prework closure artifacts incomplete"):
                write_ws7_prework_closure_packet(
                    report_dir=temp_root / "closure",
                    priorities=[{"priority": 1, "required_artifact_labels": ["missing"]}],
                    proof_artifacts=[
                        {"label": "missing", "path": temp_root / "missing.json", "priority": 1}
                    ],
                    corpora=["fls_spec"],
                )

    def test_write_ws7_prework_closure_packet_fails_when_priority_lacks_required_labels(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            artifact = temp_root / "proof.json"
            artifact.write_text('{"status": "pass"}\n', encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "missing required_artifact_labels"):
                write_ws7_prework_closure_packet(
                    report_dir=temp_root / "closure",
                    priorities=[{"priority": 1}],
                    proof_artifacts=[{"label": "proof-a", "path": artifact, "priority": 1}],
                    corpora=["fls_spec"],
                )

    def test_generate_ws7_prework_closure_packet_derives_policy_from_report_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            self._write_junit(
                temp_root,
                name="priority1_3.xml",
                testcases=[
                    *(["tests.unit.sqlite_kb.test_chunk_first_runbook_prereqs"] * 6),
                    *(["tests.unit.sqlite_kb.test_provenance_guard"] * 6),
                ],
            )
            self._write_junit(
                temp_root,
                name="priority2_5.xml",
                testcases=[
                    *(["tests.unit.sqlite_kb.test_query_rust_reference"] * 6),
                    *(["tests.unit.test_fls_step6"] * 6),
                ],
            )
            self._write_junit(
                temp_root,
                name="priority4_supporting.xml",
                testcases=[
                    *(["tests.unit.sqlite_kb.test_query_core_docs"] * 4),
                    *(["tests.unit.sqlite_kb.test_query_set_verifier"] * 4),
                ],
            )
            self._write_junit(
                temp_root,
                name="supporting.xml",
                testcases=[
                    "tests.unit.sqlite_kb.test_fls_candidate_search",
                    "tests.unit.sqlite_kb.test_fls_candidate_search",
                    "tests.unit.sqlite_kb.test_ws7_prework_closure",
                    "tests.unit.sqlite_kb.test_ws7_prework_closure",
                ],
            )

            for corpus in ("fls_spec", "core_docs", "rust_reference"):
                self._write_current_chunk_first_report(temp_root, corpus=corpus)

            with patch(
                "retrieval.services.ws7_prework_closure.load_corpus_runtime_defaults",
                side_effect=lambda root, corpus: type(
                    "Defaults",
                    (),
                    {
                        "report_root": root / ".cache" / "sqlite_kb" / "reports" / corpus,
                    },
                )(),
            ):
                report_path, manifest_path = generate_ws7_prework_closure_packet(
                    report_dir=temp_root,
                    deferred_items=["WS7 staged runtime implementation"],
                    root=temp_root,
                )

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "pass")
            self.assertEqual(
                report["closure_custody"]["artifact_freeze_mode"], "packet_local_copies"
            )
            self.assertEqual(
                report["priorities"][4]["required_artifact_labels"],
                [
                    "priority2_5",
                    "priority4_supporting",
                    "supporting",
                    "fls_spec_current_chunk_first",
                    "core_docs_current_chunk_first",
                    "rust_reference_current_chunk_first",
                ],
            )
            self.assertEqual(len(report["db_identities"]), 3)
            self.assertTrue(manifest_path.is_file())
            self.assertTrue((temp_root / "artifacts" / "priority1_3.xml").is_file())
            self.assertTrue(
                (temp_root / "artifacts" / "fls_spec_current_chunk_first.json").is_file()
            )

    def test_generate_ws7_prework_closure_packet_rejects_placeholder_junit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / "priority1_3.xml").write_text(
                '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="fake" name="fake" /></testsuite>\n',
                encoding="utf-8",
            )
            self._write_junit(
                temp_root,
                name="priority2_5.xml",
                testcases=[
                    *(["tests.unit.sqlite_kb.test_query_rust_reference"] * 6),
                    *(["tests.unit.test_fls_step6"] * 6),
                ],
            )
            self._write_junit(
                temp_root,
                name="priority4_supporting.xml",
                testcases=[
                    *(["tests.unit.sqlite_kb.test_query_core_docs"] * 4),
                    *(["tests.unit.sqlite_kb.test_query_set_verifier"] * 4),
                ],
            )
            self._write_junit(
                temp_root,
                name="supporting.xml",
                testcases=[
                    "tests.unit.sqlite_kb.test_fls_candidate_search",
                    "tests.unit.sqlite_kb.test_fls_candidate_search",
                    "tests.unit.sqlite_kb.test_ws7_prework_closure",
                    "tests.unit.sqlite_kb.test_ws7_prework_closure",
                ],
            )
            for corpus in ("fls_spec", "core_docs", "rust_reference"):
                self._write_current_chunk_first_report(temp_root, corpus=corpus)

            with patch(
                "retrieval.services.ws7_prework_closure.load_corpus_runtime_defaults",
                side_effect=lambda root, corpus: type(
                    "Defaults",
                    (),
                    {
                        "report_root": root / ".cache" / "sqlite_kb" / "reports" / corpus,
                    },
                )(),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "missing expected testcase identity|too few executed tests"
                ):
                    generate_ws7_prework_closure_packet(report_dir=temp_root, root=temp_root)

    def test_maybe_refresh_ws7_prework_closure_packet_emits_fail_packet_when_incomplete(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            self._write_current_chunk_first_report(temp_root, corpus="fls_spec")

            with patch(
                "retrieval.services.ws7_prework_closure.load_corpus_runtime_defaults",
                side_effect=lambda root, corpus: type(
                    "Defaults",
                    (),
                    {
                        "report_root": root / ".cache" / "sqlite_kb" / "reports" / corpus,
                    },
                )(),
            ):
                result = maybe_refresh_ws7_prework_closure_packet(root=temp_root)

            self.assertIsNotNone(result)
            report_path, manifest_path = result
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "fail")
            self.assertTrue(report["missing_artifacts"])
            self.assertTrue(manifest_path.is_file())


if __name__ == "__main__":
    unittest.main()
