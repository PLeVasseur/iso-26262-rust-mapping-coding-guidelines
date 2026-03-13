from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.builders.core_docs_builder import run_core_docs_build  # noqa: E402
from retrieval.build.chunk_fts_validation import refresh_chunk_fts_rowids  # noqa: E402
from retrieval.core_docs.rustdoc_extract import ParsedItem, TargetCfg  # noqa: E402
from retrieval.operations.eval import evaluate_retrieval_prompts  # noqa: E402
from retrieval.operations.query import execute_retrieval_query  # noqa: E402
from semantic_backend_client import SemanticBackendConfig  # noqa: E402
from sqlite_query_guardrails import execute_contract_query  # noqa: E402


def _cfg_signature() -> tuple[str, str]:
    payload = json.dumps(
        {
            "target_arch": "aarch64",
            "target_env": "gnu",
            "target_os": "linux",
        },
        sort_keys=True,
    )
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _target_cfg() -> TargetCfg:
    signature, signature_sha = _cfg_signature()
    return TargetCfg(
        target_triple="aarch64-unknown-linux-gnu",
        target_os="linux",
        target_arch="aarch64",
        target_env="gnu",
        cfg_signature=signature,
        cfg_signature_sha256=signature_sha,
    )


def _parsed_items() -> list[ParsedItem]:
    target = _target_cfg()
    return [
        ParsedItem(
            item_id="item-ptr-read",
            target=target,
            item_path="core::ptr::read",
            item_kind="function",
            signature="unsafe fn read<T>(src: *const T) -> T",
            stability="stable",
            safety_notes="Callers must ensure the pointer is valid before dereference.",
            panic_behavior="",
            example_snippets="let value = unsafe { core::ptr::read(ptr) };",
            docs_text=(
                "Reads the value from src without moving it. Raw pointer dereference requires "
                "explicit safety reasoning to avoid undefined behavior."
            ),
            source_anchor="https://doc.rust-lang.org/core/ptr/fn.read.html",
        ),
        ParsedItem(
            item_id="item-atomic-fence",
            target=target,
            item_path="core::sync::atomic::fence",
            item_kind="function",
            signature="fn fence(order: Ordering)",
            stability="stable",
            safety_notes="Fence ordering coordinates visibility between threads.",
            panic_behavior="",
            example_snippets="core::sync::atomic::fence(Ordering::SeqCst);",
            docs_text=(
                "Establishes an atomic fence with the given ordering so writes become visible "
                "between concurrent threads."
            ),
            source_anchor="https://doc.rust-lang.org/core/sync/atomic/fn.fence.html",
        ),
    ]


def _table_rows() -> list[dict[str, object]]:
    return [
        {
            "row_node_id": "row-1d",
            "row_idx": 4,
            "row_marker": "1d",
            "requirement_text": "Defensive handling of result and option based error flows.",
            "row_profile_terms": ["result", "option", "error", "defensive"],
        },
        {
            "row_node_id": "row-1f",
            "row_idx": 6,
            "row_marker": "1f",
            "requirement_text": "Concurrency controls and atomic synchronization behavior.",
            "row_profile_terms": ["atomic", "ordering", "thread", "concurrency"],
        },
    ]


def _seed_chunk_embeddings(db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT chunk_uid, clean_text FROM chunks ORDER BY chunk_uid ASC"
        ).fetchall()
        payload = []
        for chunk_uid, clean_text in rows:
            vector = [0.1, 0.2, 0.3]
            payload.append(
                (
                    str(chunk_uid),
                    "Qwen/Qwen3-Embedding-4B",
                    "chunk-v1",
                    hashlib.sha256(str(clean_text).lower().encode("utf-8")).hexdigest(),
                    json.dumps(vector),
                    math.sqrt(sum(value * value for value in vector)),
                    "2026-03-09T00:00:00Z",
                    "2026-03-09T00:00:00Z",
                )
            )
        connection.executemany(
            """
            INSERT INTO chunk_embeddings(
                chunk_uid,
                model_id,
                embed_version,
                text_sha256,
                vector_json,
                vector_norm,
                embedded_at,
                source_fetched_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        connection.commit()


class QueryCoreDocsTests(unittest.TestCase):
    def _build_fixture_db(self, temp_root: Path) -> tuple[Path, Path, Path]:
        db_path = temp_root / "current" / "core_docs.sqlite"
        contract_path = ROOT / "config" / "sqlite_query_contracts" / "core_docs.yaml"
        query_log_root = temp_root / "query_logs"

        fake_json = temp_root / "fake_core.json"
        fake_json.parent.mkdir(parents=True, exist_ok=True)
        fake_json.write_text("{}\n", encoding="utf-8")

        args = Namespace(
            extractor_db=str(temp_root / "extractor.sqlite"),
            table_node_id="table1-fixture",
            db_path=str(db_path),
            report_root=str(temp_root / "reports" / "core_docs"),
            reference_revision="fixture-core-docs-001",
            extractor_toolchain="nightly-aarch64-apple-darwin",
            chunk_target_min_tokens=20,
            chunk_target_max_tokens=80,
            embedding_model_id="Qwen/Qwen3-Embedding-4B",
            reranker_model_id="BAAI/bge-reranker-v2-m3",
            embedding_dim=2560,
            ingest_strategy="core_docs_rustdoc_v1",
            allow_provenance_mismatch=False,
        )

        with patch(
            "retrieval.builders.core_docs_builder.TARGET_MATRIX", (_target_cfg().target_triple,)
        ):
            with patch(
                "retrieval.builders.core_docs_builder._generate_rustdoc_json",
                return_value=fake_json,
            ):
                with patch(
                    "retrieval.builders.core_docs_builder._load_parsed_items",
                    return_value=_parsed_items(),
                ):
                    with patch(
                        "retrieval.builders.core_docs_builder._resolve_table1_rows",
                        return_value=_table_rows(),
                    ):
                        with patch(
                            "retrieval.builders.core_docs_builder._target_cfg",
                            return_value=_target_cfg(),
                        ):
                            with patch(
                                "retrieval.builders.core_docs_builder._toolchain_version",
                                return_value="rustc 1.83.1",
                            ):
                                with patch(
                                    "retrieval.builders.core_docs_builder._utc_now",
                                    return_value="2026-03-09T00:00:00+00:00",
                                ):
                                    run_core_docs_build(args=args, root=ROOT)

        _seed_chunk_embeddings(db_path)
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(
                """
                SELECT chunk_uid, section_id, section_heading, chunk_text
                FROM chunks_fts
                ORDER BY chunk_uid DESC
                """
            ).fetchall()
            connection.execute("DELETE FROM chunks_fts")
            connection.execute(
                """
                INSERT INTO chunks_fts(chunk_uid, section_id, section_heading, chunk_text)
                VALUES(?, ?, ?, ?)
                """,
                ("__ws7_dummy__", "", "", "dummy"),
            )
            connection.executemany(
                """
                INSERT INTO chunks_fts(chunk_uid, section_id, section_heading, chunk_text)
                VALUES(?, ?, ?, ?)
                """,
                rows,
            )
            connection.execute("DELETE FROM chunks_fts WHERE chunk_uid = ?", ("__ws7_dummy__",))
            refresh_chunk_fts_rowids(connection)
            connection.commit()
        return db_path, contract_path, query_log_root

    def _semantic_config(self) -> SemanticBackendConfig:
        return SemanticBackendConfig(
            base_url="http://127.0.0.1:8080",
            embed_base_url="http://127.0.0.1:8080",
            rerank_base_url="http://127.0.0.1:8081",
            embed_model_id="Qwen/Qwen3-Embedding-4B",
            reranker_model_id="BAAI/bge-reranker-v2-m3",
            timeout_sec=0.2,
        )

    def test_core_docs_contract_queries_return_direct_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path, contract_path, query_log_root = self._build_fixture_db(temp_root)

            snapshot_result = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="snapshot_metadata",
                params={},
                row_limit=1,
                query_log_root=query_log_root,
            )
            self.assertEqual(snapshot_result["row_count"], 1)
            self.assertEqual(snapshot_result["rows"][0]["commit_sha"], "fixture-core-docs-001")

            corpus_result = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="chunk_corpus_v1_all",
                params={"chunk_uid_after": ""},
                row_limit=50,
                query_log_root=query_log_root,
            )
            self.assertGreaterEqual(corpus_result["row_count"], 2)
            ptr_row = next(
                row for row in corpus_result["rows"] if row.get("item_path") == "core::ptr::read"
            )
            self.assertEqual(ptr_row["target_triple"], "aarch64-unknown-linux-gnu")

            lexical_result = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="lexical_chunk_search_v1",
                params={"fts_query": "pointer OR dereference"},
                row_limit=10,
                query_log_root=query_log_root,
            )
            self.assertIn(
                ptr_row["chunk_uid"], [row["chunk_uid"] for row in lexical_result["rows"]]
            )

            subset_result = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="lexical_chunk_search_v2_subset",
                params={
                    "fts_query": "pointer OR dereference",
                    "allowed_scope_ids_json": json.dumps([ptr_row["chunk_uid"]]),
                },
                row_limit=10,
                query_log_root=query_log_root,
            )
            self.assertEqual(
                [row["chunk_uid"] for row in subset_result["rows"]], [ptr_row["chunk_uid"]]
            )

            row_requirements = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id="table1_row_requirements_v2",
                params={"row_marker": ""},
                row_limit=10,
                query_log_root=query_log_root,
            )
            self.assertGreaterEqual(row_requirements["row_count"], 2)
            self.assertIn("profile_terms", row_requirements["rows"][0])

            with sqlite3.connect(db_path) as connection:
                mismatch_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM chunks AS c
                        JOIN chunks_fts AS f ON f.chunk_uid = c.chunk_uid
                        WHERE c.rowid != f.rowid
                        """
                    ).fetchone()[0]
                )
            self.assertGreater(mismatch_count, 0)

    def test_direct_retrieval_preserves_target_fields_across_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path, contract_path, query_log_root = self._build_fixture_db(temp_root)

            with patch(
                "retrieval.operations.query.check_semantic_backend",
                return_value={"ok": True, "checks": []},
            ):
                with patch(
                    "retrieval.query.semantic_pipeline.embed_texts",
                    side_effect=lambda _config, texts: [[0.1, 0.2, 0.3] for _ in texts],
                ):
                    with patch(
                        "retrieval.query.semantic_pipeline.rerank_texts",
                        side_effect=lambda config, query_text, documents: [
                            1.0 - (0.05 * idx) for idx, _ in enumerate(documents)
                        ],
                    ):
                        lexical = execute_retrieval_query(
                            mode="lexical",
                            db_path=db_path,
                            contract_path=contract_path,
                            query_log_root=query_log_root,
                            query_text="raw pointer dereference safety",
                            row_marker="",
                            top_k=5,
                            candidate_limit=50,
                            allow_degraded=False,
                            semantic_config=self._semantic_config(),
                            semantic_retries=0,
                            persist_semantic_cache=False,
                            corpus="core_docs",
                        )
                        semantic = execute_retrieval_query(
                            mode="semantic",
                            db_path=db_path,
                            contract_path=contract_path,
                            query_log_root=query_log_root,
                            query_text="raw pointer dereference safety",
                            row_marker="",
                            top_k=5,
                            candidate_limit=50,
                            allow_degraded=False,
                            semantic_config=self._semantic_config(),
                            semantic_retries=0,
                            persist_semantic_cache=False,
                            corpus="core_docs",
                        )
                        hybrid = execute_retrieval_query(
                            mode="hybrid",
                            db_path=db_path,
                            contract_path=contract_path,
                            query_log_root=query_log_root,
                            query_text="atomic fence ordering threads",
                            row_marker="",
                            top_k=5,
                            candidate_limit=50,
                            allow_degraded=False,
                            semantic_config=self._semantic_config(),
                            semantic_retries=0,
                            persist_semantic_cache=False,
                            corpus="core_docs",
                        )

            for result in (lexical, semantic, hybrid):
                self.assertGreaterEqual(result["row_count"], 1)
                self.assertEqual(result["scope"]["state"], "global")
                row = result["rows"][0]
                self.assertTrue(str(row.get("chunk_uid", "")).strip())
                self.assertTrue(str(row.get("item_path", "")).startswith("core::"))
                self.assertEqual(row.get("item_kind"), "function")
                self.assertEqual(row.get("target_triple"), "aarch64-unknown-linux-gnu")
                self.assertEqual(row.get("target_env"), "gnu")
                self.assertEqual(row.get("cfg_signature"), _target_cfg().cfg_signature)
                self.assertTrue(str(row.get("cfg_signature_sha256", "")).strip())
                self.assertTrue(
                    str(row.get("source_anchor", "")).startswith("https://doc.rust-lang.org/")
                )

    def test_bounded_subset_and_explicit_empty_scope_are_directly_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path, contract_path, query_log_root = self._build_fixture_db(temp_root)

            baseline = execute_retrieval_query(
                mode="lexical",
                db_path=db_path,
                contract_path=contract_path,
                query_log_root=query_log_root,
                query_text="atomic fence ordering",
                row_marker="",
                top_k=5,
                candidate_limit=50,
                allow_degraded=False,
                semantic_config=self._semantic_config(),
                semantic_retries=0,
                persist_semantic_cache=False,
                corpus="core_docs",
            )
            allowed_id = str(baseline["rows"][0]["statement_id"])

            with patch(
                "retrieval.operations.query.check_semantic_backend",
                return_value={"ok": True, "checks": []},
            ):
                with patch(
                    "retrieval.query.semantic_pipeline.embed_texts",
                    side_effect=lambda _config, texts: [[0.1, 0.2, 0.3] for _ in texts],
                ):
                    with patch(
                        "retrieval.query.semantic_pipeline.rerank_texts",
                        side_effect=lambda config, query_text, documents: [
                            1.0 - (0.05 * idx) for idx, _ in enumerate(documents)
                        ],
                    ):
                        scoped_results = [
                            execute_retrieval_query(
                                mode=mode,
                                db_path=db_path,
                                contract_path=contract_path,
                                query_log_root=query_log_root,
                                query_text="atomic fence ordering",
                                row_marker="",
                                top_k=5,
                                candidate_limit=50,
                                allow_degraded=False,
                                semantic_config=self._semantic_config(),
                                semantic_retries=0,
                                persist_semantic_cache=False,
                                corpus="core_docs",
                                allowed_statement_ids=[allowed_id, allowed_id, "", "   "],
                            )
                            for mode in ("lexical", "semantic", "hybrid")
                        ]
            for scoped in scoped_results:
                self.assertEqual(scoped["scope"]["state"], "restricted_subset")
                self.assertEqual(scoped["scope"]["scope_id_field"], "chunk_uid")
                self.assertEqual(scoped["scope"]["requested_count"], 4)
                self.assertEqual(scoped["scope"]["normalized_count"], 1)
                self.assertEqual(scoped["scope"]["matched_count"], 1)
                self.assertEqual(scoped["scope"]["allowed_scope_ids"], [allowed_id])
                self.assertTrue(
                    all(str(row["statement_id"]) == allowed_id for row in scoped["rows"])
                )

            empty_query_log_root = temp_root / "query_logs_empty"
            empty = execute_retrieval_query(
                mode="semantic",
                db_path=db_path,
                contract_path=contract_path,
                query_log_root=empty_query_log_root,
                query_text="atomic fence ordering",
                row_marker="",
                top_k=5,
                candidate_limit=50,
                allow_degraded=False,
                semantic_config=self._semantic_config(),
                semantic_retries=0,
                persist_semantic_cache=False,
                corpus="core_docs",
                allowed_statement_ids=[],
            )
            self.assertEqual(empty["row_count"], 0)
            self.assertEqual(empty["scope"]["state"], "restricted_empty")
            self.assertEqual(empty["scope"]["scope_id_field"], "chunk_uid")
            self.assertEqual(empty["scope"]["normalized_count"], 0)
            self.assertFalse(empty_query_log_root.exists())

    def test_hybrid_direct_retrieval_degrades_without_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path, contract_path, query_log_root = self._build_fixture_db(temp_root)

            with patch(
                "retrieval.operations.query.check_semantic_backend",
                return_value={"ok": False, "checks": [{"name": "embed", "ok": False}]},
            ):
                result = execute_retrieval_query(
                    mode="hybrid",
                    db_path=db_path,
                    contract_path=contract_path,
                    query_log_root=query_log_root,
                    query_text="raw pointer dereference safety",
                    row_marker="",
                    top_k=5,
                    candidate_limit=50,
                    allow_degraded=True,
                    semantic_config=self._semantic_config(),
                    semantic_retries=0,
                    persist_semantic_cache=False,
                    corpus="core_docs",
                )

            self.assertTrue(result["degraded"])
            self.assertEqual(result["executed_mode"], "lexical")
            self.assertEqual(result["degraded_reason"], "HYBRID_BACKEND_UNAVAILABLE")
            self.assertGreaterEqual(result["row_count"], 1)

    def test_core_docs_no_subset_batch_smoke_remains_intact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path, contract_path, query_log_root = self._build_fixture_db(temp_root)

            prompts = [
                {
                    "prompt_id": "CORE-DOCS-PTR-LEX",
                    "slice": "issue_identification",
                    "query_text": "raw pointer dereference safety",
                    "modes": ["lexical"],
                    "expected_row_markers": ["1d"],
                    "relevant_statement_ids": [],
                    "relevant_anchor_prefixes": ["https://doc.rust-lang.org/core/ptr/"],
                    "relevant_terms": ["pointer", "dereference", "safety"],
                    "hard_negative_statement_ids": [],
                    "semantic_focus": False,
                }
            ]

            report = evaluate_retrieval_prompts(
                db_path=db_path,
                contract_path=contract_path,
                query_log_root=query_log_root,
                prompts=prompts,
                top_k=5,
                candidate_limit=50,
                allow_degraded=False,
                semantic_config=self._semantic_config(),
                semantic_retries=0,
                enforce_gates=False,
            )

            self.assertEqual(report["summary"]["failed_cases"], 0)
            self.assertEqual(report["summary"]["total_mode_cases"], 1)

    def test_subset_mode_degraded_fallback_stays_bounded_on_core_docs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path, contract_path, query_log_root = self._build_fixture_db(temp_root)

            baseline = execute_retrieval_query(
                mode="lexical",
                db_path=db_path,
                contract_path=contract_path,
                query_log_root=query_log_root,
                query_text="raw pointer dereference safety",
                row_marker="",
                top_k=5,
                candidate_limit=50,
                allow_degraded=False,
                semantic_config=self._semantic_config(),
                semantic_retries=0,
                persist_semantic_cache=False,
                corpus="core_docs",
            )
            allowed_id = str(baseline["rows"][0]["statement_id"])

            with patch(
                "retrieval.operations.query.check_semantic_backend",
                return_value={"ok": False, "checks": [{"name": "embed", "ok": False}]},
            ):
                result = execute_retrieval_query(
                    mode="hybrid",
                    db_path=db_path,
                    contract_path=contract_path,
                    query_log_root=query_log_root,
                    query_text="raw pointer dereference safety",
                    row_marker="",
                    top_k=5,
                    candidate_limit=50,
                    allow_degraded=True,
                    semantic_config=self._semantic_config(),
                    semantic_retries=0,
                    persist_semantic_cache=False,
                    corpus="core_docs",
                    allowed_statement_ids=[allowed_id, allowed_id, "", "   "],
                )

            self.assertTrue(result["degraded"])
            self.assertEqual(result["executed_mode"], "lexical")
            self.assertEqual(result["scope"]["state"], "restricted_subset")
            self.assertTrue(all(str(row["statement_id"]) == allowed_id for row in result["rows"]))


if __name__ == "__main__":
    unittest.main()
