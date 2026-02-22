from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[3]
THIS_DIR = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from _fixture import create_reference_fixture  # noqa: E402

from semantic_backend_client import SemanticBackendConfig  # noqa: E402
from sqlite_build_rust_reference import (  # noqa: E402
    DEFAULT_EXTRACTOR_DB,
    DEFAULT_TABLE_NODE_ID,
    RETRIEVAL_CORPUS_VALUES,
    build_rust_reference_db,
    parse_args,
)
from sqlite_eval_rust_reference_retrieval import evaluate_retrieval_prompts  # noqa: E402
from sqlite_materialize_rust_reference_embeddings import main as materialize_main  # noqa: E402
from sqlite_query_rust_reference import execute_retrieval_query  # noqa: E402


class ChunkFirstRunbookPrereqsTests(unittest.TestCase):
    def _build_fixture_db(self, temp_root: Path) -> Path:
        db_path = temp_root / "current" / "rust_reference.sqlite"
        snapshot_root = temp_root / "snapshots"
        manifest_path = temp_root / "manifest.yaml"
        reference_source_dir = create_reference_fixture(temp_root)

        build_rust_reference_db(
            db_path=db_path,
            snapshot_root=snapshot_root,
            manifest_path=manifest_path,
            extractor_db=DEFAULT_EXTRACTOR_DB,
            table_node_id=DEFAULT_TABLE_NODE_ID,
            reference_source_dir=reference_source_dir,
            reference_revision="fixture-001",
            min_sections=4,
            min_statements=8,
            min_mechanisms=4,
        )
        return db_path

    def _query_ids_from_logs(self, query_log_root: Path) -> set[str]:
        ids: set[str] = set()
        for log_path in sorted(query_log_root.glob("*.jsonl")):
            for raw_line in log_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                query_id = str(payload.get("query_id", "")).strip()
                if query_id:
                    ids.add(query_id)
        return ids

    def _build_long_sentence_fixture(self, temp_root: Path) -> Path:
        source_root = create_reference_fixture(temp_root)
        src = source_root / "src"

        sentences = [
            (
                f"Sentence {idx} documents deterministic ingestion behavior and "
                "preserves explicit defensive result option error semantics in long form text."
            )
            for idx in range(1, 33)
        ]
        types_path = src / "types.md"
        existing = types_path.read_text(encoding="utf-8")
        types_path.write_text(
            existing.rstrip() + "\n\n" + " ".join(sentences) + "\n",
            encoding="utf-8",
        )
        return source_root

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

    def test_retrieval_corpus_default_is_chunk(self) -> None:
        with patch.object(sys, "argv", ["sqlite_build_rust_reference.py"]):
            args = parse_args()
        self.assertEqual(args.retrieval_corpus, "chunk")

    def test_build_requires_pinned_reference_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "current" / "rust_reference.sqlite"
            snapshot_root = temp_root / "snapshots"
            manifest_path = temp_root / "manifest.yaml"
            reference_source_dir = create_reference_fixture(temp_root)

            with self.assertRaises(ValueError):
                build_rust_reference_db(
                    db_path=db_path,
                    snapshot_root=snapshot_root,
                    manifest_path=manifest_path,
                    extractor_db=DEFAULT_EXTRACTOR_DB,
                    table_node_id=DEFAULT_TABLE_NODE_ID,
                    reference_source_dir=reference_source_dir,
                    reference_revision=None,
                    min_sections=4,
                    min_statements=8,
                    min_mechanisms=4,
                )

    def test_build_has_no_hidden_sentence_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "current" / "rust_reference.sqlite"
            snapshot_root = temp_root / "snapshots"
            manifest_path = temp_root / "manifest.yaml"
            reference_source_dir = self._build_long_sentence_fixture(temp_root)

            build_rust_reference_db(
                db_path=db_path,
                snapshot_root=snapshot_root,
                manifest_path=manifest_path,
                extractor_db=DEFAULT_EXTRACTOR_DB,
                table_node_id=DEFAULT_TABLE_NODE_ID,
                reference_source_dir=reference_source_dir,
                reference_revision="fixture-001",
                min_sections=1,
                min_statements=20,
                min_mechanisms=1,
            )

            connection = sqlite3.connect(db_path)
            try:
                statement_count = int(connection.execute("SELECT COUNT(*) FROM statements").fetchone()[0])
            finally:
                connection.close()

            self.assertGreaterEqual(statement_count, 30)

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

    def test_build_populates_chunk_schema_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = self._build_fixture_db(temp_root)

            connection = sqlite3.connect(db_path)
            try:
                user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                table_names = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                chunk_count = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
                span_count = int(connection.execute("SELECT COUNT(*) FROM chunk_spans").fetchone()[0])
                docs_count = int(connection.execute("SELECT COUNT(*) FROM docs").fetchone()[0])
            finally:
                connection.close()

            self.assertEqual(user_version, 6)
            self.assertTrue({"kb_metadata", "docs", "chunks", "chunk_spans"}.issubset(table_names))
            self.assertGreaterEqual(chunk_count, 1)
            self.assertGreaterEqual(span_count, chunk_count)
            self.assertGreaterEqual(docs_count, 1)

    def test_chunk_uids_are_stable_for_same_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_a = self._build_fixture_db(temp_root / "run_a")
            db_b = self._build_fixture_db(temp_root / "run_b")

            def _read_chunk_ids(path: Path) -> list[str]:
                connection = sqlite3.connect(path)
                try:
                    return [
                        str(row[0])
                        for row in connection.execute(
                            "SELECT chunk_uid FROM chunks ORDER BY chunk_uid ASC"
                        ).fetchall()
                    ]
                finally:
                    connection.close()

            self.assertEqual(_read_chunk_ids(db_a), _read_chunk_ids(db_b))

    def test_chunks_stay_within_section_and_token_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = self._build_fixture_db(temp_root)

            connection = sqlite3.connect(db_path)
            try:
                rows = connection.execute(
                    """
                    SELECT
                        c.chunk_uid,
                        c.section_id,
                        c.token_len,
                        COUNT(DISTINCT sp.source_anchor) AS anchor_count
                    FROM chunks AS c
                    LEFT JOIN chunk_spans AS sp ON sp.chunk_uid = c.chunk_uid
                    GROUP BY c.chunk_uid, c.section_id, c.token_len
                    ORDER BY c.chunk_uid ASC
                    """
                ).fetchall()
            finally:
                connection.close()

            self.assertGreaterEqual(len(rows), 1)
            for _, _, token_len, anchor_count in rows:
                self.assertGreater(int(token_len), 0)
                self.assertLessEqual(int(token_len), 500)
                self.assertEqual(int(anchor_count), 1)

    def test_query_path_uses_chunk_query_ids_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = self._build_fixture_db(temp_root)
            contract_path = ROOT / "config" / "sqlite_query_contracts" / "rust_reference_chunk.yaml"
            query_log_root = temp_root / "query_logs_query"

            result = execute_retrieval_query(
                mode="lexical",
                db_path=db_path,
                contract_path=contract_path,
                query_log_root=query_log_root,
                query_text="defensive error result option",
                row_marker="",
                top_k=5,
                candidate_limit=200,
                allow_degraded=False,
                semantic_config=SemanticBackendConfig(
                    base_url="http://127.0.0.1:1",
                    embed_model_id="unused",
                    reranker_model_id="unused",
                    timeout_sec=0.2,
                ),
                semantic_retries=0,
                persist_semantic_cache=False,
            )
            self.assertGreaterEqual(int(result.get("row_count", 0)), 1)

            query_ids = self._query_ids_from_logs(query_log_root)
            self.assertTrue(
                {
                    "chunk_corpus_v1_all",
                    "lexical_chunk_search_v1",
                    "table1_row_requirements_v2",
                }.issubset(query_ids)
            )
            self.assertEqual(
                {
                    "statement_corpus_v3_all",
                    "lexical_statement_search_v2",
                    "table1_row_requirements_v1",
                }.intersection(query_ids),
                set(),
            )

    def test_materialize_and_eval_execute_on_chunk_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = self._build_fixture_db(temp_root)
            contract_path = ROOT / "config" / "sqlite_query_contracts" / "rust_reference_chunk.yaml"

            materialize_query_log_root = temp_root / "query_logs_materialize"
            materialize_progress = temp_root / "reports" / "materialize_progress.jsonl"
            with patch(
                "sqlite_materialize_rust_reference_embeddings.embed_texts",
                side_effect=lambda _config, texts: [[0.1, 0.2, 0.3] for _ in texts],
            ):
                with patch.object(
                    sys,
                    "argv",
                    [
                        "sqlite_materialize_rust_reference_embeddings.py",
                        "--db-path",
                        str(db_path),
                        "--contract-path",
                        str(contract_path),
                        "--query-log-root",
                        str(materialize_query_log_root),
                        "--row-marker",
                        "1a",
                        "--allow-partial-corpus",
                        "--batch-size",
                        "4",
                        "--semantic-retries",
                        "0",
                        "--progress-log-path",
                        str(materialize_progress),
                    ],
                ):
                    self.assertEqual(materialize_main(), 0)

            materialize_query_ids = self._query_ids_from_logs(materialize_query_log_root)
            self.assertTrue(
                {"chunk_corpus_v1_all", "table1_row_requirements_v2"}.issubset(
                    materialize_query_ids
                )
            )
            self.assertEqual(
                {
                    "statement_corpus_v3_all",
                    "table1_row_requirements_v1",
                }.intersection(materialize_query_ids),
                set(),
            )

            eval_query_log_root = temp_root / "query_logs_eval"
            prompts = [
                {
                    "prompt_id": "RET-CHUNK-001",
                    "slice": "issue_identification",
                    "query_text": "defensive error result option",
                    "modes": ["lexical"],
                    "expected_row_markers": ["1d"],
                    "relevant_statement_ids": [],
                    "relevant_anchor_prefixes": [],
                    "relevant_terms": ["error", "result"],
                    "hard_negative_statement_ids": [],
                    "min_metrics": {},
                    "semantic_focus": False,
                }
            ]

            _ = evaluate_retrieval_prompts(
                db_path=db_path,
                contract_path=contract_path,
                query_log_root=eval_query_log_root,
                prompts=prompts,
                top_k=5,
                candidate_limit=200,
                allow_degraded=False,
                semantic_config=SemanticBackendConfig(
                    base_url="http://127.0.0.1:1",
                    embed_model_id="unused",
                    reranker_model_id="unused",
                    timeout_sec=0.2,
                ),
                semantic_retries=0,
                enforce_gates=False,
            )

            eval_query_ids = self._query_ids_from_logs(eval_query_log_root)
            self.assertTrue(
                {
                    "chunk_corpus_v1_all",
                    "lexical_chunk_search_v1",
                    "table1_row_requirements_v2",
                }.issubset(eval_query_ids)
            )
            self.assertEqual(
                {
                    "statement_corpus_v3_all",
                    "lexical_statement_search_v2",
                    "table1_row_requirements_v1",
                }.intersection(eval_query_ids),
                set(),
            )


if __name__ == "__main__":
    unittest.main()
