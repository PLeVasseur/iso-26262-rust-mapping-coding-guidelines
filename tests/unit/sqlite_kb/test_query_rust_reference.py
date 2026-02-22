from __future__ import annotations

import json
import socket
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

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
    build_rust_reference_db,
)
from sqlite_query_guardrails import GuardrailError, execute_contract_query  # noqa: E402
from sqlite_query_rust_reference import (  # noqa: E402
    ModeExecutionError,
    build_review_artifact_payload,
    execute_retrieval_query,
    persist_review_artifact,
    resolve_review_artifact_path,
)


class _MockSemanticHandler(BaseHTTPRequestHandler):
    def _write_json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/health", "/"}:
            self._write_json({"status": "ok"})
            return
        self._write_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        payload = json.loads(body)

        if self.path == "/v1/embeddings":
            inputs = payload.get("input") or []
            rows = [{"embedding": [0.2, 0.1, 0.3]} for _ in inputs]
            self._write_json({"data": rows})
            return

        if self.path == "/v1/rerank":
            documents = payload.get("documents") or []
            rows = [
                {"index": idx, "relevance_score": 1.0 - (idx * 0.01)}
                for idx, _ in enumerate(documents)
            ]
            self._write_json({"results": rows})
            return

        self._write_json({"error": "not found"}, status=404)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        _ = format
        _ = args


class QueryRustReferenceTests(unittest.TestCase):
    def _start_mock_backend(self) -> tuple[HTTPServer, threading.Thread, str]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            host, port = sock.getsockname()

        server = HTTPServer((host, port), _MockSemanticHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, f"http://{host}:{port}"

    def _build_fixture_db(self, temp_root: Path) -> tuple[Path, Path, Path]:
        db_path = temp_root / "current" / "rust_reference.sqlite"
        snapshot_root = temp_root / "snapshots"
        manifest_path = temp_root / "manifest.yaml"
        query_log_root = temp_root / "query_logs"
        reference_source_dir = create_reference_fixture(temp_root)
        contract_path = ROOT / "config" / "sqlite_query_contracts" / "rust_reference.yaml"

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
        return db_path, contract_path, query_log_root

    def test_lexical_mode_is_deterministic_and_query_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path, contract_path, query_log_root = self._build_fixture_db(temp_root)

            defensive = execute_retrieval_query(
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
            defensive_again = execute_retrieval_query(
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
            concurrency = execute_retrieval_query(
                mode="lexical",
                db_path=db_path,
                contract_path=contract_path,
                query_log_root=query_log_root,
                query_text="send sync thread concurrency",
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

            defensive_ids = [row["statement_id"] for row in defensive["rows"]]
            defensive_again_ids = [row["statement_id"] for row in defensive_again["rows"]]
            concurrency_ids = [row["statement_id"] for row in concurrency["rows"]]

            self.assertEqual(defensive_ids, defensive_again_ids)
            self.assertNotEqual(defensive_ids[0], concurrency_ids[0])
            self.assertGreaterEqual(int(defensive["rows"][0].get("token_overlap_count", 0)), 1)

            defensive_texts = " ".join(
                str(row.get("statement_text", "")).lower() for row in defensive["rows"][:3]
            )
            concurrency_texts = " ".join(
                str(row.get("statement_text", "")).lower() for row in concurrency["rows"][:3]
            )
            self.assertTrue(any(term in defensive_texts for term in {"error", "result", "option"}))
            self.assertTrue(any(term in concurrency_texts for term in {"send", "sync", "thread"}))

    def test_row_projection_includes_evidence_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path, contract_path, query_log_root = self._build_fixture_db(temp_root)

            result = execute_retrieval_query(
                mode="lexical",
                db_path=db_path,
                contract_path=contract_path,
                query_log_root=query_log_root,
                query_text="error handling result option defensive",
                row_marker="",
                top_k=8,
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

            self.assertGreaterEqual(len(result["row_projection"]), 1)
            top_row = result["row_projection"][0]
            self.assertIn("evidence_trace", top_row)
            self.assertGreaterEqual(len(top_row["evidence_trace"]), 1)

            trace_head = top_row["evidence_trace"][0]
            self.assertTrue(str(trace_head["statement_id"]).strip())
            self.assertTrue(str(trace_head["source_anchor"]).strip())
            self.assertGreater(float(trace_head["contribution"]), 0.0)
            self.assertEqual(top_row["top_statement_id"], trace_head["statement_id"])
            self.assertEqual(top_row["top_source_anchor"], trace_head["source_anchor"])

    def test_review_artifact_path_conflict_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            with self.assertRaises(GuardrailError):
                resolve_review_artifact_path(
                    root=temp_root,
                    mode="hybrid",
                    query_text="How should Rust code handle defensive error paths safely?",
                    prompt_id="RET-RESOLVE-001",
                    save_response_path="artifact.json",
                    save_response_dir="artifacts",
                )

    def test_review_artifact_writer_persists_required_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            target_path = resolve_review_artifact_path(
                root=temp_root,
                mode="hybrid",
                query_text="How should Rust code handle defensive error paths safely?",
                prompt_id="RET-RESOLVE-001",
                save_response_path="",
                save_response_dir="review-artifacts",
            )
            self.assertIsNotNone(target_path)

            payload = build_review_artifact_payload(
                mode="hybrid",
                query_text="How should Rust code handle defensive error paths safely?",
                row_marker="1d",
                prompt_id="RET-RESOLVE-001",
                top_k=8,
                candidate_limit=200,
                include_score_breakdown=True,
                allow_degraded=False,
                db_path=temp_root / "db.sqlite",
                contract_path=temp_root / "contract.yaml",
                query_log_root=temp_root / "query_logs",
                semantic_config=SemanticBackendConfig(
                    base_url="http://127.0.0.1:19080",
                    embed_model_id="Qwen/Qwen3-Embedding-4B",
                    reranker_model_id="BAAI/bge-reranker-v2-m3",
                    timeout_sec=60.0,
                    embed_base_url="http://127.0.0.1:19080",
                    rerank_base_url="http://127.0.0.1:19081",
                ),
                semantic_retries=0,
                persist_semantic_cache=True,
                allow_online_corpus_embedding=False,
                response={
                    "requested_mode": "hybrid",
                    "executed_mode": "hybrid",
                    "degraded": False,
                    "query_text": "How should Rust code handle defensive error paths safely?",
                    "row_marker": "1d",
                    "row_count": 1,
                    "duration_ms": 12.0,
                    "row_projection": [],
                    "rows": [],
                },
            )
            persist_review_artifact(target_path, payload)

            written = json.loads(target_path.read_text(encoding="utf-8"))
            self.assertEqual(written["schema_version"], 1)
            self.assertEqual(written["prompt_id"], "RET-RESOLVE-001")
            self.assertEqual(written["query"]["mode"], "hybrid")
            self.assertEqual(written["query"]["row_marker"], "1d")
            self.assertIn("runtime", written)
            self.assertIn("semantic", written["runtime"])
            self.assertEqual(
                written["runtime"]["semantic"]["embed_model_id"],
                "Qwen/Qwen3-Embedding-4B",
            )
            self.assertIn("response", written)

    def test_row_verdicts_query_returns_all_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "current" / "rust_reference.sqlite"
            snapshot_root = temp_root / "snapshots"
            manifest_path = temp_root / "manifest.yaml"
            query_log_root = temp_root / "query_logs"
            contract_path = ROOT / "config" / "sqlite_query_contracts" / "rust_reference.yaml"
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

            result = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="row_verdicts_for_table1",
                params={},
                query_log_root=query_log_root,
            )
            markers = {row["row_marker"] for row in result["rows"]}
            expected = {f"1{chr(ord('a') + idx)}" for idx in range(9)}

            self.assertEqual(result["row_count"], 9)
            self.assertEqual(markers, expected)
            self.assertTrue(all(str(row["rationale_timestamp"]).strip() for row in result["rows"]))

            metadata = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="snapshot_metadata",
                params={},
                query_log_root=query_log_root,
            )
            self.assertEqual(metadata["row_count"], 1)
            self.assertTrue(str(metadata["rows"][0]["fetched_at"]).strip())

            coverage = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="document_timestamp_coverage",
                params={},
                query_log_root=query_log_root,
            )
            self.assertEqual(coverage["row_count"], 1)
            row = coverage["rows"][0]
            self.assertEqual(int(row["missing_fetched_at"]), 0)
            self.assertEqual(int(row["missing_commit_sha"]), 0)

            semantic_models = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="semantic_model_metadata",
                params={},
                query_log_root=query_log_root,
            )
            self.assertGreaterEqual(semantic_models["row_count"], 2)
            self.assertEqual(
                {model["model_role"] for model in semantic_models["rows"]},
                {"embedder", "reranker"},
            )

            first_row_node_id = result["rows"][0]["row_node_id"]
            score_breakdown = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="row_mechanism_score_breakdown",
                params={"row_node_id": first_row_node_id},
                query_log_root=query_log_root,
            )
            self.assertGreaterEqual(score_breakdown["row_count"], 1)
            self.assertTrue(
                all(
                    "hybrid_score" in score_row and "semantic_score" in score_row
                    for score_row in score_breakdown["rows"]
                )
            )

            lexical_v2 = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="lexical_statement_search_v2",
                params={"fts_query": "defensive OR error OR handling"},
                query_log_root=query_log_root,
            )
            self.assertGreaterEqual(lexical_v2["row_count"], 1)
            self.assertIn("bm25_raw", lexical_v2["rows"][0])

            corpus_v3 = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="statement_corpus_v3_all",
                params={"statement_id_after": ""},
                query_log_root=query_log_root,
                row_limit=5000,
            )
            connection = sqlite3.connect(db_path)
            try:
                statement_count = int(
                    connection.execute("SELECT COUNT(*) FROM statements").fetchone()[0]
                )
            finally:
                connection.close()

            self.assertEqual(corpus_v3["row_count"], statement_count)

            with self.assertRaises(GuardrailError):
                execute_contract_query(
                    db_path=db_path,
                    contract_path=contract_path,
                    query_id="statement_corpus_v2",
                    params={"row_marker": ""},
                    query_log_root=query_log_root,
                )

    def test_semantic_mode_fails_loud_when_backend_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "current" / "rust_reference.sqlite"
            snapshot_root = temp_root / "snapshots"
            manifest_path = temp_root / "manifest.yaml"
            query_log_root = temp_root / "query_logs"
            contract_path = ROOT / "config" / "sqlite_query_contracts" / "rust_reference.yaml"
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

            with self.assertRaises(ModeExecutionError) as raised:
                execute_retrieval_query(
                    mode="semantic",
                    db_path=db_path,
                    contract_path=contract_path,
                    query_log_root=query_log_root,
                    query_text="How does Rust support defensive programming?",
                    row_marker="",
                    top_k=5,
                    candidate_limit=200,
                    allow_degraded=False,
                    semantic_config=SemanticBackendConfig(
                        base_url="http://127.0.0.1:1",
                        embed_model_id="Qwen/Qwen3-Embedding-4B",
                        reranker_model_id="BAAI/bge-reranker-v2-m3",
                        timeout_sec=0.5,
                    ),
                    semantic_retries=0,
                    persist_semantic_cache=False,
                )

            self.assertEqual(raised.exception.code, "SEMANTIC_BACKEND_UNAVAILABLE")

    def test_semantic_mode_fails_fast_when_index_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "current" / "rust_reference.sqlite"
            snapshot_root = temp_root / "snapshots"
            manifest_path = temp_root / "manifest.yaml"
            query_log_root = temp_root / "query_logs"
            contract_path = ROOT / "config" / "sqlite_query_contracts" / "rust_reference.yaml"
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

            server, thread, base_url = self._start_mock_backend()
            try:
                with self.assertRaises(ModeExecutionError) as raised:
                    execute_retrieval_query(
                        mode="semantic",
                        db_path=db_path,
                        contract_path=contract_path,
                        query_log_root=query_log_root,
                        query_text="How does Rust support defensive programming?",
                        row_marker="",
                        top_k=5,
                        candidate_limit=200,
                        allow_degraded=False,
                        semantic_config=SemanticBackendConfig(
                            base_url=base_url,
                            embed_model_id="Qwen/Qwen3-Embedding-4B",
                            reranker_model_id="BAAI/bge-reranker-v2-m3",
                            timeout_sec=1.0,
                        ),
                        semantic_retries=0,
                        persist_semantic_cache=True,
                    )
            finally:
                server.shutdown()
                thread.join(timeout=2.0)
                server.server_close()

            self.assertEqual(raised.exception.code, "SEMANTIC_INDEX_INCOMPLETE")

    def test_semantic_mode_materializes_embedding_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "current" / "rust_reference.sqlite"
            snapshot_root = temp_root / "snapshots"
            manifest_path = temp_root / "manifest.yaml"
            query_log_root = temp_root / "query_logs"
            contract_path = ROOT / "config" / "sqlite_query_contracts" / "rust_reference.yaml"
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

            server, thread, base_url = self._start_mock_backend()
            try:
                result = execute_retrieval_query(
                    mode="semantic",
                    db_path=db_path,
                    contract_path=contract_path,
                    query_log_root=query_log_root,
                    query_text="How does Rust support defensive programming?",
                    row_marker="",
                    top_k=5,
                    candidate_limit=200,
                    allow_degraded=False,
                    semantic_config=SemanticBackendConfig(
                        base_url=base_url,
                        embed_model_id="Qwen/Qwen3-Embedding-4B",
                        reranker_model_id="BAAI/bge-reranker-v2-m3",
                        timeout_sec=1.0,
                    ),
                    semantic_retries=0,
                    persist_semantic_cache=True,
                    allow_online_corpus_embedding=True,
                )
            finally:
                server.shutdown()
                thread.join(timeout=2.0)
                server.server_close()

            self.assertEqual(result["executed_mode"], "semantic")
            self.assertGreaterEqual(result["row_count"], 1)

            connection = sqlite3.connect(db_path)
            try:
                cached = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM statement_embeddings WHERE model_id = ?",
                        ("Qwen/Qwen3-Embedding-4B",),
                    ).fetchone()[0]
                )
                statement_count = int(
                    connection.execute("SELECT COUNT(*) FROM statements").fetchone()[0]
                )
            finally:
                connection.close()

            self.assertEqual(cached, statement_count)

    def test_hybrid_returns_final_score_and_candidate_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "current" / "rust_reference.sqlite"
            snapshot_root = temp_root / "snapshots"
            manifest_path = temp_root / "manifest.yaml"
            query_log_root = temp_root / "query_logs"
            contract_path = ROOT / "config" / "sqlite_query_contracts" / "rust_reference_chunk.yaml"
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
                retrieval_corpus="chunk",
            )

            server, thread, base_url = self._start_mock_backend()
            try:
                result = execute_retrieval_query(
                    mode="hybrid",
                    db_path=db_path,
                    contract_path=contract_path,
                    query_log_root=query_log_root,
                    query_text="defensive error result option",
                    row_marker="",
                    top_k=5,
                    candidate_limit=200,
                    allow_degraded=False,
                    semantic_config=SemanticBackendConfig(
                        base_url=base_url,
                        embed_model_id="Qwen/Qwen3-Embedding-4B",
                        reranker_model_id="BAAI/bge-reranker-v2-m3",
                        timeout_sec=1.0,
                    ),
                    semantic_retries=0,
                    persist_semantic_cache=True,
                    allow_online_corpus_embedding=True,
                )
            finally:
                server.shutdown()
                thread.join(timeout=2.0)
                server.server_close()

            self.assertEqual(result["executed_mode"], "hybrid")
            self.assertIn("score_definitions", result)
            self.assertIn("candidate_generation", result)
            self.assertGreaterEqual(int(result.get("row_count", 0)), 1)

            rows = list(result.get("rows", []))
            self.assertTrue(all("final_score" in row for row in rows))
            self.assertTrue(all("lexical_score" in row for row in rows))
            self.assertTrue(all("semantic_score" in row for row in rows))
            self.assertTrue(all("reranker_score" in row for row in rows))

            final_scores = [float(row.get("final_score", 0.0)) for row in rows]
            self.assertEqual(final_scores, sorted(final_scores, reverse=True))

    def test_guardrails_reject_forbidden_write_sql(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "current" / "rust_reference.sqlite"
            snapshot_root = temp_root / "snapshots"
            manifest_path = temp_root / "manifest.yaml"
            bad_contract = temp_root / "bad_contract.yaml"
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

            bad_contract.write_text(
                """
version: 1
database: rust_reference
queries:
  illegal_write:
    params: []
    row_limit: 10
    sql: |
      DELETE FROM table1_rows
                """.strip(),
                encoding="utf-8",
            )

            with self.assertRaises(GuardrailError):
                execute_contract_query(
                    db_path=db_path,
                    contract_path=bad_contract,
                    query_id="illegal_write",
                    params={},
                )


if __name__ == "__main__":
    unittest.main()
