from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.eval.human_report import HumanReportConfig, generate_human_report  # noqa: E402
from retrieval.eval.human_report_resolvers.registry import get_human_report_resolver  # noqa: E402


def _build_fixture_db(db_path: Path, *, with_core_docs_metadata: bool) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE source_documents (
              document_id INTEGER PRIMARY KEY,
              rel_path TEXT NOT NULL
            );
            CREATE TABLE sections (
              section_id INTEGER PRIMARY KEY,
              document_id INTEGER NOT NULL,
              heading TEXT,
              anchor TEXT
            );
            CREATE TABLE chunks (
              chunk_uid TEXT PRIMARY KEY,
              section_id INTEGER NOT NULL,
              clean_text TEXT
            );
            CREATE TABLE chunk_spans (
              chunk_uid TEXT NOT NULL,
              span_order INTEGER NOT NULL,
              source_anchor TEXT
            );
            """
        )
        if with_core_docs_metadata:
            conn.executescript(
                """
                CREATE TABLE core_docs_chunk_metadata (
                  chunk_uid TEXT PRIMARY KEY,
                  item_path TEXT,
                  item_kind TEXT,
                  target_triple TEXT
                );
                """
            )

        conn.execute("INSERT INTO source_documents(document_id, rel_path) VALUES(1, 'doc/path.md')")
        conn.execute(
            "INSERT INTO sections(section_id, document_id, heading, anchor) "
            "VALUES(1, 1, 'Example Heading', 'sec')"
        )
        conn.execute(
            "INSERT INTO chunks(chunk_uid, section_id, clean_text) VALUES(?, 1, ?)",
            ("chunk::abc", "Example chunk text for review."),
        )
        conn.execute(
            "INSERT INTO chunk_spans(chunk_uid, span_order, source_anchor) VALUES('chunk::abc', 1, 'https://example.test/doc#sec')"
        )
        if with_core_docs_metadata:
            conn.execute(
                "INSERT INTO core_docs_chunk_metadata("
                "chunk_uid, item_path, item_kind, target_triple"
                ") VALUES('chunk::abc', 'core::option::Option', 'enum', "
                "'aarch64-unknown-linux-gnu')"
            )


def _write_eval(eval_path: Path, db_path: Path) -> None:
    payload = {
        "inputs": {"db_path": str(db_path)},
        "summary": {
            "total_mode_cases": 3,
            "passed_cases": 3,
            "failed_cases": 0,
            "enforce_gates": True,
            "gate_failures": [],
        },
        "cases": [
            {
                "prompt_id": "P1",
                "mode": "lexical",
                "status": "pass",
                "slice": "issue_identification",
                "query_text": "sample query",
                "top_statement_ids": ["chunk::abc"],
                "mrr_at_k": 1.0,
                "precision_at_k": 1.0,
                "ndcg_at_k": 1.0,
                "row_hit_rate": 1.0,
                "abstain_active": False,
                "expect_abstain": False,
            },
            {
                "prompt_id": "P1",
                "mode": "semantic",
                "status": "pass",
                "slice": "issue_identification",
                "query_text": "sample query",
                "top_statement_ids": ["chunk::abc"],
                "mrr_at_k": 1.0,
                "precision_at_k": 1.0,
                "ndcg_at_k": 1.0,
                "row_hit_rate": 1.0,
                "abstain_active": False,
                "expect_abstain": False,
            },
            {
                "prompt_id": "P1",
                "mode": "hybrid",
                "status": "pass",
                "slice": "issue_identification",
                "query_text": "sample query",
                "top_statement_ids": ["chunk::abc"],
                "mrr_at_k": 1.0,
                "precision_at_k": 1.0,
                "ndcg_at_k": 1.0,
                "row_hit_rate": 1.0,
                "abstain_active": False,
                "expect_abstain": False,
            },
        ],
    }
    eval_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_testset(path: Path) -> None:
    path.write_text(
        """
version: 1
suite_id: test_suite
prompts:
  - prompt_id: P1
    slice: issue_identification
    query_text: sample query
    expected_row_markers: [1d]
    expect_abstain: false
""".strip()
        + "\n",
        encoding="utf-8",
    )


class EvalReportHumanReviewTests(unittest.TestCase):
    def test_core_docs_report_includes_core_docs_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "core_docs.sqlite"
            eval_path = temp_root / "eval.json"
            testset_path = temp_root / "testset.yaml"
            out_path = temp_root / "report.md"

            _build_fixture_db(db_path, with_core_docs_metadata=True)
            _write_eval(eval_path, db_path)
            _write_testset(testset_path)

            resolver = get_human_report_resolver("core_docs")
            config = HumanReportConfig(
                eval_path=eval_path,
                db_path=db_path,
                output_path=out_path,
                testset_path=testset_path,
                top_n=3,
                snippet_chars=200,
                only_problem_prompts=False,
            )
            generate_human_report(resolver=resolver, config=config)

            rendered = out_path.read_text(encoding="utf-8")
            self.assertIn("## P1", rendered)
            self.assertIn("item_path", rendered)
            self.assertIn("core::option::Option", rendered)

    def test_rust_reference_report_includes_doc_path_column(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "rust_reference.sqlite"
            eval_path = temp_root / "eval.json"
            testset_path = temp_root / "testset.yaml"
            out_path = temp_root / "report.md"

            _build_fixture_db(db_path, with_core_docs_metadata=False)
            _write_eval(eval_path, db_path)
            _write_testset(testset_path)

            resolver = get_human_report_resolver("rust_reference")
            config = HumanReportConfig(
                eval_path=eval_path,
                db_path=db_path,
                output_path=out_path,
                testset_path=testset_path,
                top_n=3,
                snippet_chars=200,
                only_problem_prompts=False,
            )
            generate_human_report(resolver=resolver, config=config)

            rendered = out_path.read_text(encoding="utf-8")
            self.assertIn("## P1", rendered)
            self.assertIn("doc_path", rendered)
            self.assertIn("doc/path.md", rendered)


if __name__ == "__main__":
    unittest.main()
