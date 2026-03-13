from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
THIS_DIR = Path(__file__).resolve().parent
TESTS_UNIT = ROOT / "tests" / "unit"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
if str(TESTS_UNIT) not in sys.path:
    sys.path.insert(0, str(TESTS_UNIT))

from _fixture import create_reference_fixture  # noqa: E402
from scripts.build_fls_db import build_fls_db  # noqa: E402
from test_fls_step6 import (  # noqa: E402
    _write_paragraph_ids,
    _write_sample_fls_source,
    _write_spec_lock,
)
from test_query_core_docs import _parsed_items, _table_rows, _target_cfg  # noqa: E402

from retrieval.build.cli import parse_build_args  # noqa: E402
from retrieval.build.core_docs_incremental import delete_core_docs_documents  # noqa: E402
from retrieval.build.incremental_refresh import (  # noqa: E402
    IncrementalFallbackRequired,
    InventoryEntry,
    build_fls_inventory,
    build_reference_inventory,
    embedding_reuse_key,
    load_source_inventory_documents,
    plan_inventory_delta,
    prepare_staged_db,
    promote_staged_db,
    replace_source_inventory,
    subtree_invalidation_rules,
    validate_incremental_audits,
    write_refresh_contract_report,
)
from retrieval.build.reports import validate_chunk_first_db  # noqa: E402
from retrieval.builders.core_docs_builder import run_core_docs_build  # noqa: E402
from retrieval.operations.build import (  # noqa: E402
    DEFAULT_EXTRACTOR_DB,
    DEFAULT_TABLE_NODE_ID,
    build_rust_reference_db,
)
from retrieval.operations.query import execute_retrieval_query  # noqa: E402
from semantic_backend_client import SemanticBackendConfig  # noqa: E402


class IncrementalRefreshTests(unittest.TestCase):
    def _semantic_config(self) -> SemanticBackendConfig:
        return SemanticBackendConfig(
            base_url="http://127.0.0.1:8080",
            embed_base_url="http://127.0.0.1:8080",
            rerank_base_url="http://127.0.0.1:8081",
            embed_model_id="Qwen/Qwen3-Embedding-4B",
            reranker_model_id="BAAI/bge-reranker-v2-m3",
            timeout_sec=0.2,
        )

    def _table_digest(self, db_path: Path, query: str) -> str:
        connection = sqlite3.connect(db_path)
        try:
            rows = connection.execute(query).fetchall()
        finally:
            connection.close()
        return json.dumps(rows, sort_keys=True, default=str)

    def test_build_cli_defaults_to_incremental_and_allows_opt_out(self) -> None:
        argv = [
            "sqlite_build.py",
            "--corpus",
            "rust_reference",
            "--reference-revision",
            "rev",
        ]
        original = list(sys.argv)
        try:
            sys.argv = argv
            args = parse_build_args(
                default_extractor_db=Path("extractor.sqlite"),
                default_table_node_id="table-id",
                default_reference_cache_dir=".cache/ref",
                default_reference_repo_url="https://example.invalid/ref.git",
                default_retrieval_mode="hybrid",
                retrieval_corpus_values=("statement", "chunk"),
                default_retrieval_corpus="chunk",
                default_semantic_profile_version="v1",
                default_embedding_model_id="embed-model",
                default_embedding_model_revision="embed-rev",
                default_embedding_model_license="license",
                default_embedding_dim=1,
                default_reranker_model_id="rerank-model",
                default_reranker_model_revision="rerank-rev",
                default_reranker_model_license="license",
            )
            self.assertTrue(args.incremental)

            sys.argv = argv + ["--no-incremental"]
            args = parse_build_args(
                default_extractor_db=Path("extractor.sqlite"),
                default_table_node_id="table-id",
                default_reference_cache_dir=".cache/ref",
                default_reference_repo_url="https://example.invalid/ref.git",
                default_retrieval_mode="hybrid",
                retrieval_corpus_values=("statement", "chunk"),
                default_retrieval_corpus="chunk",
                default_semantic_profile_version="v1",
                default_embedding_model_id="embed-model",
                default_embedding_model_revision="embed-rev",
                default_embedding_model_license="license",
                default_embedding_dim=1,
                default_reranker_model_id="rerank-model",
                default_reranker_model_revision="rerank-rev",
                default_reranker_model_license="license",
            )
            self.assertFalse(args.incremental)
        finally:
            sys.argv = original

    def test_reference_and_fls_inventory_builders_capture_expected_units(self) -> None:
        reference_docs = [
            SimpleNamespace(
                document_id="doc::one",
                rel_path="one.md",
                title="One",
                chapter_id="chapter:001",
                doc_order=1,
                source_sha256="doc-sha",
            )
        ]
        reference_sections = [
            SimpleNamespace(
                section_id="sec::one",
                document_id="doc::one",
                heading="Heading",
                anchor="anchor",
                order_index=1,
                level=2,
                source_sha256="sec-sha",
            )
        ]
        reference_statements = [
            SimpleNamespace(
                statement_id="stmt::one",
                section_id="sec::one",
                statement_type="constraint",
                sentence_index=0,
                source_sha256="stmt-sha",
            )
        ]
        reference_chunks = [
            SimpleNamespace(
                chunk_uid="chunk::one",
                section_id="sec::one",
                order_index=1,
                token_len=10,
                source_sha256="chunk-sha",
            )
        ]
        doc_inventory, section_inventory, unit_inventory = build_reference_inventory(
            documents=reference_docs,
            sections=reference_sections,
            statements=reference_statements,
            chunks=reference_chunks,
        )
        self.assertIn("doc::one", doc_inventory)
        self.assertIn("sec::one", section_inventory)
        self.assertIn(("statement", "stmt::one"), unit_inventory)
        self.assertIn(("chunk", "chunk::one"), unit_inventory)

        fls_paragraphs = [
            SimpleNamespace(
                paragraph_id="fls_1",
                paragraph_number="1",
                source_file="src/doc.rst",
                document_link="doc.html",
                section_link="doc.html#sec",
                section_id="sec",
                checksum="p-sha",
            )
        ]
        doc_inventory, section_inventory, unit_inventory = build_fls_inventory(
            paragraphs=fls_paragraphs
        )
        self.assertIn("doc.html", doc_inventory)
        self.assertIn("doc.html#sec", section_inventory)
        self.assertIn(("paragraph", "fls_1"), unit_inventory)
        self.assertIn(("chunk", "fls_1"), unit_inventory)

    def test_embedding_reuse_key_depends_on_stable_id_content_and_model_version(self) -> None:
        key_a = embedding_reuse_key(
            stable_id="chunk::1",
            content_sha256="sha-a",
            model_id="model-a",
            embed_version="v1",
        )
        key_b = embedding_reuse_key(
            stable_id="chunk::1",
            content_sha256="sha-b",
            model_id="model-a",
            embed_version="v1",
        )
        key_c = embedding_reuse_key(
            stable_id="chunk::1",
            content_sha256="sha-a",
            model_id="model-a",
            embed_version="v2",
        )
        self.assertNotEqual(key_a, key_b)
        self.assertNotEqual(key_a, key_c)

    def test_refresh_contract_report_documents_source_and_dependent_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            path = write_refresh_contract_report(
                report_root=temp_root,
                corpus="rust_reference",
                run_id="run-1",
            )
            self.assertTrue(path.is_file())
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("statements", payload["source_tables"])
            self.assertIn("semantic_corpus", payload["dependent_tables"])
            self.assertEqual(subtree_invalidation_rules(corpus="fls_spec")["root_unit"], "document")

    def test_delete_core_docs_documents_removes_stale_chunk_embeddings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "core_docs.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE source_documents(document_id TEXT PRIMARY KEY);
                    CREATE TABLE docs(doc_uid TEXT PRIMARY KEY);
                    CREATE TABLE sections(section_id TEXT PRIMARY KEY, document_id TEXT);
                    CREATE TABLE chunks(chunk_uid TEXT PRIMARY KEY, section_id TEXT);
                    CREATE TABLE chunk_spans(
                        chunk_uid TEXT,
                        source_anchor TEXT,
                        start_offset INTEGER,
                        end_offset INTEGER,
                        span_order INTEGER
                    );
                    CREATE TABLE chunks_fts(
                        chunk_uid TEXT,
                        section_id TEXT,
                        section_heading TEXT,
                        chunk_text TEXT
                    );
                    CREATE TABLE chunk_embeddings(
                        chunk_uid TEXT,
                        model_id TEXT,
                        embed_version TEXT,
                        text_sha256 TEXT,
                        vector_json TEXT,
                        vector_norm REAL,
                        embedded_at TEXT,
                        source_fetched_at TEXT
                    );
                    CREATE TABLE core_docs_chunk_metadata(chunk_uid TEXT);
                    """
                )
                connection.execute("INSERT INTO source_documents VALUES('doc-1')")
                connection.execute("INSERT INTO docs VALUES('doc-1')")
                connection.execute("INSERT INTO sections VALUES('sec-1', 'doc-1')")
                connection.execute("INSERT INTO chunks VALUES('chunk-1', 'sec-1')")
                connection.execute(
                    "INSERT INTO chunk_embeddings VALUES("
                    "'chunk-1', 'm', 'v1', 'sha', '[]', 1.0, 'now', 'now'"
                    ")"
                )
                connection.execute("INSERT INTO core_docs_chunk_metadata VALUES('chunk-1')")
                connection.commit()
                delete_core_docs_documents(connection, ["doc-1"])
                remaining = connection.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[
                    0
                ]
            finally:
                connection.close()
            self.assertEqual(int(remaining), 0)

    def test_validate_incremental_audits_requires_embedding_and_provenance_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stage.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE snapshots(snapshot_id TEXT);
                    CREATE TABLE materialization_deltas(
                        run_id TEXT PRIMARY KEY,
                        corpus TEXT,
                        mode TEXT,
                        base_snapshot_id TEXT,
                        target_snapshot_id TEXT,
                        delta_json TEXT,
                        created_at TEXT
                    );
                    CREATE TABLE embedding_reuse_audit(
                        run_id TEXT,
                        corpus TEXT,
                        model_fingerprint TEXT,
                        reused_count INTEGER,
                        recomputed_count INTEGER,
                        audited_at TEXT
                    );
                    """
                )
                connection.execute("INSERT INTO snapshots VALUES('snap-1')")
                connection.execute(
                    "INSERT INTO materialization_deltas VALUES(?, ?, ?, ?, ?, ?, ?)",
                    ("run-1", "fls_spec", "incremental", "", "snap-1", "{}", "now"),
                )
                connection.commit()
            finally:
                connection.close()
            report = validate_incremental_audits(staged_db_path=db_path, corpus="fls_spec")
            self.assertFalse(report["passed"])
            self.assertIn("missing_embedding_reuse_audit", report["failures"])

    def test_plan_inventory_delta_classifies_changes(self) -> None:
        current = {
            "doc:a": InventoryEntry("doc:a", "sha-a", "meta-a"),
            "doc:b": InventoryEntry("doc:b", "sha-b", "meta-b"),
        }
        incoming = {
            "doc:a": InventoryEntry("doc:a", "sha-a", "meta-a"),
            "doc:b": InventoryEntry("doc:b", "sha-b2", "meta-b"),
            "doc:c": InventoryEntry("doc:c", "sha-c", "meta-c"),
        }
        plan = plan_inventory_delta(current=current, incoming=incoming)
        self.assertEqual(plan.unchanged, ("doc:a",))
        self.assertEqual(plan.updated, ("doc:b",))
        self.assertEqual(plan.added, ("doc:c",))
        self.assertEqual(plan.deleted, ())

    def test_stage_prepare_promote_and_inventory_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            live_db = temp_root / "current" / "core_docs.sqlite"
            live_db.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(live_db)
            try:
                connection.executescript(
                    """
                    CREATE TABLE source_inventory_documents (
                        corpus TEXT NOT NULL,
                        document_id TEXT NOT NULL,
                        snapshot_id TEXT NOT NULL,
                        source_path TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL,
                        metadata_sha256 TEXT NOT NULL,
                        last_materialized_at TEXT NOT NULL,
                        PRIMARY KEY(corpus, document_id)
                    );
                    CREATE TABLE source_inventory_sections (
                        corpus TEXT NOT NULL,
                        section_id TEXT NOT NULL,
                        document_id TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL,
                        metadata_sha256 TEXT NOT NULL,
                        last_materialized_at TEXT NOT NULL,
                        PRIMARY KEY(corpus, section_id)
                    );
                    CREATE TABLE source_inventory_units (
                        corpus TEXT NOT NULL,
                        unit_kind TEXT NOT NULL,
                        unit_id TEXT NOT NULL,
                        parent_id TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL,
                        metadata_sha256 TEXT NOT NULL,
                        derived_from_sha256 TEXT NOT NULL,
                        retrieval_eligible INTEGER NOT NULL,
                        last_materialized_at TEXT NOT NULL,
                        PRIMARY KEY(corpus, unit_kind, unit_id)
                    );
                    """
                )
                replace_source_inventory(
                    connection,
                    corpus="core_docs",
                    snapshot_id="snap-1",
                    documents={
                        "doc:a": InventoryEntry("doc:a", "sha-a", "meta-a", parent_id="core/a.md")
                    },
                    sections={},
                    units={},
                    materialized_at="2026-03-10T00:00:00+00:00",
                )
                connection.commit()
            finally:
                connection.close()

            staged_db, copied = prepare_staged_db(
                live_db_path=live_db,
                staged_root=temp_root / "staged",
                corpus="core_docs",
                run_id="run-1",
            )
            self.assertTrue(copied)
            self.assertTrue(staged_db.is_file())

            staged_connection = sqlite3.connect(staged_db)
            try:
                loaded = load_source_inventory_documents(staged_connection, corpus="core_docs")
            finally:
                staged_connection.close()
            self.assertEqual(set(loaded), {"doc:a"})

            promotion = promote_staged_db(
                live_db_path=live_db,
                staged_db_path=staged_db,
                promotion_root=temp_root / "promotions",
                corpus="core_docs",
                run_id="run-1",
            )
            self.assertTrue(Path(promotion["promoted_copy_path"]).is_file())
            self.assertTrue(Path(promotion["rollback_path"]).is_file())

    def test_build_fls_db_incremental_updates_existing_shadow_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_dir = temp_root / "fls_source"
            db_path = temp_root / "current" / "fls_spec.db"
            spec_lock = temp_root / "spec.lock"
            topology = temp_root / "paragraph-ids.json"
            _write_sample_fls_source(source_dir)
            _write_spec_lock(spec_lock)
            _write_paragraph_ids(topology)

            first = build_fls_db(
                source_dir=source_dir,
                db_path=db_path,
                spec_lock_path=spec_lock,
                topology_path=topology,
                compat_symlink_mode="never",
                report_root=temp_root / "reports" / "fls_spec",
            )
            self.assertTrue(Path(first["chunk_first_report_path"]).is_file())

            chapter_file = source_dir / "concurrency.rst"
            chapter_file.write_text(
                chapter_file.read_text(encoding="utf-8").replace(
                    "Atomic fence ordering controls visibility between threads.",
                    "Atomic fence ordering controls visibility between audited threads.",
                ),
                encoding="utf-8",
            )
            second = build_fls_db(
                source_dir=source_dir,
                db_path=db_path,
                spec_lock_path=spec_lock,
                topology_path=topology,
                compat_symlink_mode="never",
                report_root=temp_root / "reports" / "fls_spec",
                incremental=True,
                staged_output_root=temp_root / "staged",
                promotion_root=temp_root / "promotions",
            )
            self.assertTrue(second["incremental"])
            self.assertTrue(Path(second["delta_report_path"]).is_file())
            dry_run_payload = json.loads(
                Path(second["dry_run_report_path"]).read_text(encoding="utf-8")
            )
            self.assertIn("embedding_impact", dry_run_payload)
            self.assertGreaterEqual(
                int(dry_run_payload["embedding_impact"]["changed_roots"]["updated"]),
                1,
            )
            self.assertTrue(Path(second["promotion"]["rollback_path"]).is_file())
            chunk_first_report = validate_chunk_first_db(db_path, corpus="fls_spec")
            self.assertTrue(chunk_first_report["passed"], chunk_first_report)
            connection = sqlite3.connect(db_path)
            try:
                row = connection.execute(
                    "SELECT COUNT(*) FROM paragraphs WHERE clean_text LIKE '%audited threads%'"
                ).fetchone()
            finally:
                connection.close()
            self.assertIsNotNone(row)
            self.assertGreaterEqual(int(row[0] or 0), 1)

    def test_build_fls_db_incremental_matches_full_rebuild_for_same_final_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_dir = temp_root / "fls_source"
            db_incremental = temp_root / "current" / "fls_spec.db"
            db_full = temp_root / "expected" / "fls_spec.db"
            spec_lock = temp_root / "spec.lock"
            topology = temp_root / "paragraph-ids.json"
            _write_sample_fls_source(source_dir)
            _write_spec_lock(spec_lock)
            _write_paragraph_ids(topology)

            build_fls_db(
                source_dir=source_dir,
                db_path=db_incremental,
                spec_lock_path=spec_lock,
                topology_path=topology,
                compat_symlink_mode="never",
                report_root=temp_root / "reports" / "fls_inc",
            )
            chapter_file = source_dir / "concurrency.rst"
            chapter_file.write_text(
                chapter_file.read_text(encoding="utf-8").replace(
                    "The language provides concurrency facilities with :t:`thread` safety.",
                    "The language provides concurrency facilities with audited :t:`thread` safety.",
                ),
                encoding="utf-8",
            )

            build_fls_db(
                source_dir=source_dir,
                db_path=db_incremental,
                spec_lock_path=spec_lock,
                topology_path=topology,
                compat_symlink_mode="never",
                report_root=temp_root / "reports" / "fls_inc",
                incremental=True,
                staged_output_root=temp_root / "staged",
                promotion_root=temp_root / "promotions",
            )
            build_fls_db(
                source_dir=source_dir,
                db_path=db_full,
                spec_lock_path=spec_lock,
                topology_path=topology,
                compat_symlink_mode="never",
                report_root=temp_root / "reports" / "fls_full",
            )

            queries = [
                (
                    "SELECT document_link, title, ordinal, informational "
                    "FROM fls_documents ORDER BY document_link"
                ),
                (
                    "SELECT section_link, section_id, document_link, title, number, ordinal, "
                    "informational FROM fls_sections ORDER BY section_link"
                ),
                (
                    "SELECT paragraph_id, paragraph_number, chapter, section, clean_text, "
                    "retrieval_eligible, retrieval_status FROM paragraphs ORDER BY paragraph_id"
                ),
                "SELECT paragraph_id, note FROM fls_paragraph_audit ORDER BY paragraph_id",
                (
                    "SELECT chunk_uid, section_id, clean_text, order_index "
                    "FROM chunks ORDER BY chunk_uid"
                ),
            ]
            for query in queries:
                self.assertEqual(
                    self._table_digest(db_incremental, query),
                    self._table_digest(db_full, query),
                    query,
                )

    def test_rust_reference_incremental_smoke_query_matches_full_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_root = create_reference_fixture(temp_root / "fixture")
            db_incremental = temp_root / "current" / "rust_reference.sqlite"
            db_full = temp_root / "expected" / "rust_reference.sqlite"
            manifest_inc = temp_root / "manifest_inc.yaml"
            manifest_full = temp_root / "manifest_full.yaml"

            incremental_summary = build_rust_reference_db(
                db_path=db_incremental,
                snapshot_root=temp_root / "snapshots_inc",
                manifest_path=manifest_inc,
                extractor_db=DEFAULT_EXTRACTOR_DB,
                table_node_id=DEFAULT_TABLE_NODE_ID,
                reference_source_dir=source_root,
                reference_revision="fixture-001",
                min_sections=4,
                min_statements=8,
                min_mechanisms=4,
            )
            unsafe_path = source_root / "src" / "unsafe.md"
            unsafe_path.write_text(
                unsafe_path.read_text(encoding="utf-8").replace(
                    "Unsafe code must be reviewed with clear rationale for each boundary.",
                    "Unsafe code must be reviewed with clear rationale for each audited boundary.",
                ),
                encoding="utf-8",
            )
            build_rust_reference_db(
                db_path=db_incremental,
                snapshot_root=temp_root / "snapshots_inc",
                manifest_path=manifest_inc,
                extractor_db=DEFAULT_EXTRACTOR_DB,
                table_node_id=DEFAULT_TABLE_NODE_ID,
                reference_source_dir=source_root,
                reference_revision="fixture-001",
                min_sections=4,
                min_statements=8,
                min_mechanisms=4,
                incremental=True,
                staged_output_root=temp_root / "staged_rr",
                promotion_root=temp_root / "promotions_rr",
            )
            dry_run_payload = json.loads(
                Path(incremental_summary["dry_run_report_path"]).read_text(encoding="utf-8")
            )
            self.assertIn("embedding_impact", dry_run_payload)
            chunk_first_report = validate_chunk_first_db(db_incremental, corpus="rust_reference")
            self.assertTrue(chunk_first_report["passed"], chunk_first_report)
            build_rust_reference_db(
                db_path=db_full,
                snapshot_root=temp_root / "snapshots_full",
                manifest_path=manifest_full,
                extractor_db=DEFAULT_EXTRACTOR_DB,
                table_node_id=DEFAULT_TABLE_NODE_ID,
                reference_source_dir=source_root,
                reference_revision="fixture-001",
                min_sections=4,
                min_statements=8,
                min_mechanisms=4,
            )
            contract = ROOT / "config" / "sqlite_query_contracts" / "rust_reference.yaml"
            query_log_root = temp_root / "query_logs"
            incremental_result = execute_retrieval_query(
                mode="lexical",
                db_path=db_incremental,
                contract_path=contract,
                query_log_root=query_log_root,
                query_text="unsafe reviewed rationale audited boundary",
                row_marker="",
                top_k=3,
                candidate_limit=20,
                allow_degraded=False,
                semantic_config=self._semantic_config(),
                semantic_retries=0,
                persist_semantic_cache=False,
                corpus="rust_reference",
            )
            full_result = execute_retrieval_query(
                mode="lexical",
                db_path=db_full,
                contract_path=contract,
                query_log_root=query_log_root,
                query_text="unsafe reviewed rationale audited boundary",
                row_marker="",
                top_k=3,
                candidate_limit=20,
                allow_degraded=False,
                semantic_config=self._semantic_config(),
                semantic_retries=0,
                persist_semantic_cache=False,
                corpus="rust_reference",
            )
            self.assertEqual(
                incremental_result["rows"][0]["statement_id"],
                full_result["rows"][0]["statement_id"],
            )

    def test_core_docs_incremental_smoke_query_matches_full_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fake_json = temp_root / "fake_core.json"
            fake_json.write_text("{}\n", encoding="utf-8")

            def build_core_docs_fixture(
                db_path: Path,
                report_root: Path,
                parsed_items: list[object],
                *,
                incremental: bool,
            ) -> None:
                args = SimpleNamespace(
                    extractor_db=str(temp_root / "extractor.sqlite"),
                    table_node_id="table1-fixture",
                    db_path=str(db_path),
                    report_root=str(report_root),
                    reference_revision="fixture-core-docs-001",
                    extractor_toolchain="nightly-aarch64-apple-darwin",
                    chunk_target_min_tokens=20,
                    chunk_target_max_tokens=80,
                    embedding_model_id="Qwen/Qwen3-Embedding-4B",
                    reranker_model_id="BAAI/bge-reranker-v2-m3",
                    embedding_dim=2560,
                    ingest_strategy="core_docs_rustdoc_v1",
                    allow_provenance_mismatch=False,
                    incremental=incremental,
                    force_rebuild=False,
                    staged_output_root=str(temp_root / "staged_core"),
                    promotion_root=str(temp_root / "promotions_core"),
                    refresh_derived_only=False,
                )
                with (
                    patch(
                        "retrieval.builders.core_docs_builder.TARGET_MATRIX",
                        (_target_cfg().target_triple,),
                    ),
                    patch(
                        "retrieval.builders.core_docs_builder._generate_rustdoc_json",
                        return_value=fake_json,
                    ),
                    patch(
                        "retrieval.builders.core_docs_builder._load_parsed_items",
                        return_value=parsed_items,
                    ),
                    patch(
                        "retrieval.builders.core_docs_builder._resolve_table1_rows",
                        return_value=_table_rows(),
                    ),
                    patch(
                        "retrieval.builders.core_docs_builder._target_cfg",
                        return_value=_target_cfg(),
                    ),
                    patch(
                        "retrieval.builders.core_docs_builder._toolchain_version",
                        return_value="rustc 1.83.1",
                    ),
                    patch(
                        "retrieval.builders.core_docs_builder._utc_now",
                        return_value="2026-03-09T00:00:00+00:00",
                    ),
                ):
                    run_core_docs_build(args=args, root=ROOT)

            baseline_items = _parsed_items()
            incremental_db = temp_root / "current" / "core_docs.sqlite"
            full_db = temp_root / "expected" / "core_docs.sqlite"
            build_core_docs_fixture(
                incremental_db,
                temp_root / "reports" / "core_inc",
                baseline_items,
                incremental=False,
            )
            mutated = list(_parsed_items())
            first = mutated[0]
            mutated[0] = first.__class__(
                **{
                    **first.__dict__,
                    "docs_text": first.docs_text
                    + " Audited pointer dereference reasoning remains required.",
                }
            )
            time.sleep(1.1)
            build_core_docs_fixture(
                incremental_db,
                temp_root / "reports" / "core_inc",
                mutated,
                incremental=True,
            )
            chunk_first_report = validate_chunk_first_db(incremental_db, corpus="core_docs")
            self.assertTrue(chunk_first_report["passed"], chunk_first_report)
            build_core_docs_fixture(
                full_db,
                temp_root / "reports" / "core_full",
                mutated,
                incremental=False,
            )
            contract = ROOT / "config" / "sqlite_query_contracts" / "core_docs.yaml"
            query_log_root = temp_root / "query_logs"
            incremental_result = execute_retrieval_query(
                mode="lexical",
                db_path=incremental_db,
                contract_path=contract,
                query_log_root=query_log_root,
                query_text="audited pointer dereference reasoning",
                row_marker="",
                top_k=3,
                candidate_limit=20,
                allow_degraded=False,
                semantic_config=self._semantic_config(),
                semantic_retries=0,
                persist_semantic_cache=False,
                corpus="core_docs",
            )
            full_result = execute_retrieval_query(
                mode="lexical",
                db_path=full_db,
                contract_path=contract,
                query_log_root=query_log_root,
                query_text="audited pointer dereference reasoning",
                row_marker="",
                top_k=3,
                candidate_limit=20,
                allow_degraded=False,
                semantic_config=self._semantic_config(),
                semantic_retries=0,
                persist_semantic_cache=False,
                corpus="core_docs",
            )
            self.assertEqual(
                incremental_result["rows"][0]["chunk_uid"],
                full_result["rows"][0]["chunk_uid"],
            )

    def test_build_fls_db_requires_force_rebuild_for_incremental_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_dir = temp_root / "fls_source"
            db_path = temp_root / "current" / "fls_spec.db"
            spec_lock = temp_root / "spec.lock"
            topology = temp_root / "paragraph-ids.json"
            _write_sample_fls_source(source_dir)
            _write_spec_lock(spec_lock)
            _write_paragraph_ids(topology)
            build_fls_db(
                source_dir=source_dir,
                db_path=db_path,
                spec_lock_path=spec_lock,
                topology_path=topology,
                compat_symlink_mode="never",
                report_root=temp_root / "reports" / "fls_spec",
            )
            with patch(
                "scripts.build_fls_db.validate_staged_corpus", side_effect=RuntimeError("boom")
            ):
                with self.assertRaises(IncrementalFallbackRequired):
                    build_fls_db(
                        source_dir=source_dir,
                        db_path=db_path,
                        spec_lock_path=spec_lock,
                        topology_path=topology,
                        compat_symlink_mode="never",
                        report_root=temp_root / "reports" / "fls_spec",
                        incremental=True,
                        force_rebuild=False,
                        staged_output_root=temp_root / "staged",
                        promotion_root=temp_root / "promotions",
                    )

    def test_build_fls_db_force_rebuild_fallback_and_promotion_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_dir = temp_root / "fls_source"
            db_path = temp_root / "current" / "fls_spec.db"
            spec_lock = temp_root / "spec.lock"
            topology = temp_root / "paragraph-ids.json"
            _write_sample_fls_source(source_dir)
            _write_spec_lock(spec_lock)
            _write_paragraph_ids(topology)
            build_fls_db(
                source_dir=source_dir,
                db_path=db_path,
                spec_lock_path=spec_lock,
                topology_path=topology,
                compat_symlink_mode="never",
                report_root=temp_root / "reports" / "fls_spec",
            )
            with patch(
                "scripts.build_fls_db.validate_staged_corpus", side_effect=RuntimeError("boom")
            ):
                fallback = build_fls_db(
                    source_dir=source_dir,
                    db_path=db_path,
                    spec_lock_path=spec_lock,
                    topology_path=topology,
                    compat_symlink_mode="never",
                    report_root=temp_root / "reports" / "fls_spec",
                    incremental=True,
                    force_rebuild=True,
                    staged_output_root=temp_root / "staged",
                    promotion_root=temp_root / "promotions",
                )
            self.assertFalse(fallback["incremental"])

            chapter_file = source_dir / "concurrency.rst"
            chapter_file.write_text(
                chapter_file.read_text(encoding="utf-8").replace(
                    "Atomic fence ordering controls visibility between threads.",
                    "Atomic fence ordering controls visibility between validated threads.",
                ),
                encoding="utf-8",
            )
            summary = build_fls_db(
                source_dir=source_dir,
                db_path=db_path,
                spec_lock_path=spec_lock,
                topology_path=topology,
                compat_symlink_mode="never",
                report_root=temp_root / "reports" / "fls_spec",
                incremental=True,
                force_rebuild=False,
                staged_output_root=temp_root / "staged",
                promotion_root=temp_root / "promotions",
            )
            self.assertTrue(summary["incremental"])
            provenance_path = Path(summary["promotion"]["promotion_provenance_path"])
            self.assertTrue(provenance_path.is_file())
            payload = json.loads(provenance_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["corpus"], "fls_spec")
            cross_db_report_path = Path(str(payload["cross_db_report_path"]))
            self.assertTrue(cross_db_report_path.is_file())
            cross_db_payload = json.loads(cross_db_report_path.read_text(encoding="utf-8"))
            self.assertTrue(cross_db_payload["unchanged_uninvolved"])
            self.assertIn("inventory_summary", cross_db_payload["baseline"]["core_docs"])
            self.assertIn("row_count_summary", cross_db_payload["baseline"]["core_docs"])
            self.assertIn("validation_summary", cross_db_payload["baseline"]["rust_reference"])
            self.assertEqual(
                cross_db_payload["baseline"]["core_docs"],
                cross_db_payload["current"]["core_docs"],
            )
            self.assertEqual(
                {"fls_spec", "core_docs", "rust_reference", "guidelines_repo"},
                set(cross_db_payload["per_corpus_validation"]),
            )
            fls_stage_db_path = str(
                cross_db_payload["per_corpus_validation"]["fls_spec"]["db_path"]
            )
            self.assertIn("/staged/", fls_stage_db_path)
            self.assertTrue(fls_stage_db_path.endswith("fls_spec.db"))
            operator_summary_path = Path(summary["promotion"]["operator_summary_path"])
            self.assertTrue(operator_summary_path.is_file())
            operator_summary = json.loads(operator_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(operator_summary["status"], "promoted")
            self.assertTrue(Path(summary["promotion"]["rollback_path"]).is_file())

    def test_build_rust_reference_incremental_matches_full_rebuild_for_same_final_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_root = create_reference_fixture(temp_root / "fixture")
            db_incremental = temp_root / "current" / "rust_reference.sqlite"
            db_full = temp_root / "expected" / "rust_reference.sqlite"

            build_rust_reference_db(
                db_path=db_incremental,
                snapshot_root=temp_root / "snapshots_inc",
                manifest_path=temp_root / "manifest_inc.yaml",
                extractor_db=DEFAULT_EXTRACTOR_DB,
                table_node_id=DEFAULT_TABLE_NODE_ID,
                reference_source_dir=source_root,
                reference_revision="fixture-001",
                min_sections=4,
                min_statements=8,
                min_mechanisms=4,
            )
            concurrency_path = source_root / "src" / "concurrency.md"
            concurrency_path.write_text(
                concurrency_path.read_text(encoding="utf-8").replace(
                    "Atomic operations can be used for lock-free synchronization.",
                    "Atomic operations can be used for audited lock-free synchronization.",
                ),
                encoding="utf-8",
            )
            build_rust_reference_db(
                db_path=db_incremental,
                snapshot_root=temp_root / "snapshots_inc",
                manifest_path=temp_root / "manifest_inc.yaml",
                extractor_db=DEFAULT_EXTRACTOR_DB,
                table_node_id=DEFAULT_TABLE_NODE_ID,
                reference_source_dir=source_root,
                reference_revision="fixture-001",
                min_sections=4,
                min_statements=8,
                min_mechanisms=4,
                incremental=True,
                staged_output_root=temp_root / "staged_rr",
                promotion_root=temp_root / "promotions_rr",
            )
            build_rust_reference_db(
                db_path=db_full,
                snapshot_root=temp_root / "snapshots_full",
                manifest_path=temp_root / "manifest_full.yaml",
                extractor_db=DEFAULT_EXTRACTOR_DB,
                table_node_id=DEFAULT_TABLE_NODE_ID,
                reference_source_dir=source_root,
                reference_revision="fixture-001",
                min_sections=4,
                min_statements=8,
                min_mechanisms=4,
            )

            queries = [
                (
                    "SELECT document_id, rel_path, title, source_sha256 "
                    "FROM source_documents ORDER BY document_id"
                ),
                (
                    "SELECT section_id, document_id, heading, text, source_sha256 "
                    "FROM sections ORDER BY section_id"
                ),
                (
                    "SELECT statement_id, section_id, statement_type, text, source_sha256 "
                    "FROM statements ORDER BY statement_id"
                ),
                (
                    "SELECT chunk_uid, section_id, clean_text, source_sha256 "
                    "FROM chunks ORDER BY chunk_uid"
                ),
                (
                    "SELECT mechanism_id, canonical_symbol, mechanism_family "
                    "FROM mechanisms ORDER BY mechanism_id"
                ),
                (
                    "SELECT corpus_id, source_kind, source_id, row_node_id, mechanism_id, "
                    "text_sha256 FROM semantic_corpus ORDER BY corpus_id"
                ),
            ]
            for query in queries:
                self.assertEqual(
                    self._table_digest(db_incremental, query),
                    self._table_digest(db_full, query),
                    query,
                )


if __name__ == "__main__":
    unittest.main()
