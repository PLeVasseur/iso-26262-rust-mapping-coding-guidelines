from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path

import pytest
from context.fls_lookup import (
    get_live_topology_membership,
    resolve_fls_for_construct,
    validate_fls_id,
)
from context.fls_search_runtime import search_fls_paragraphs
from scripts.build_fls_db import build_fls_db
from scripts.fetch_fls_source import fetch_fls_source
from scripts.parse_fls_paragraphs import parse_fls_rst
from scripts.retrieval.operations.query import execute_retrieval_query
from semantic_backend_client import SemanticBackendConfig


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
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z",
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


def _write_sample_fls_source(source_dir: Path) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "concurrency.rst").write_text(
        """
Concurrency
===========

.. _fls_chapter_anchor:

.. _fls_sendsync001:

:dp:`fls_sendsync001`
The language provides concurrency facilities with :t:`thread` safety.

.. _fls_atomic002:

:dp:`fls_atomic002`
Atomic fence ordering controls visibility between threads.
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (source_dir / "unsafety.rst").write_text(
        """
Unsafety
========

.. _fls_unsafe003:

:dp:`fls_unsafe003`
Raw pointer dereference may cause undefined behavior.
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (source_dir / "_metadata.json").write_text(
        json.dumps({"commit_sha": "sample-sha"}),
        encoding="utf-8",
    )


def _write_spec_lock(path: Path) -> None:
    payload = {
        "documents": [
            {
                "title": "Concurrency",
                "sections": [
                    {
                        "id": "fls_chapter_anchor",
                        "paragraphs": [
                            {"id": "fls_sendsync001", "number": "17:1"},
                            {"id": "fls_atomic002", "number": "17.1:4"},
                        ],
                    }
                ],
            },
            {
                "title": "Unsafety",
                "sections": [
                    {
                        "id": "fls_unsafety_anchor",
                        "paragraphs": [{"id": "fls_unsafe003", "number": "19:2"}],
                    }
                ],
            },
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_paragraph_ids(path: Path) -> None:
    payload = {
        "documents": [
            {
                "title": "Concurrency",
                "link": "concurrency.html",
                "informational": False,
                "sections": [
                    {
                        "id": "fls_chapter_anchor",
                        "number": "17",
                        "title": "Concurrency",
                        "link": "concurrency.html",
                        "informational": False,
                        "paragraphs": [
                            {
                                "id": "fls_sendsync001",
                                "number": "17:1",
                                "link": "concurrency.html#fls_sendsync001",
                                "checksum": "checksum-send-sync",
                            },
                            {
                                "id": "fls_atomic002",
                                "number": "17.1:4",
                                "link": "concurrency.html#fls_atomic002",
                                "checksum": "checksum-atomic",
                            },
                        ],
                    }
                ],
            },
            {
                "title": "Unsafety",
                "link": "unsafety.html",
                "informational": False,
                "sections": [
                    {
                        "id": "fls_unsafety_anchor",
                        "number": "19",
                        "title": "Unsafety",
                        "link": "unsafety.html",
                        "informational": False,
                        "paragraphs": [
                            {
                                "id": "fls_unsafe003",
                                "number": "19:2",
                                "link": "unsafety.html#fls_unsafe003",
                                "checksum": "checksum-unsafe",
                            }
                        ],
                    }
                ],
            },
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_parse_fls_rst_extracts_dp_paragraphs(tmp_path: Path) -> None:
    source_dir = tmp_path / "fls_source"
    _write_sample_fls_source(source_dir)

    paragraphs = parse_fls_rst(
        source_dir / "concurrency.rst",
        paragraph_numbers={"fls_sendsync001": "17:1", "fls_atomic002": "17.1:4"},
    )
    assert len(paragraphs) == 2
    assert paragraphs[0].paragraph_id == "fls_sendsync001"
    assert paragraphs[0].paragraph_number == "17:1"
    assert paragraphs[0].chapter == "Concurrency"
    assert ":t:`thread`" in paragraphs[0].raw_text
    assert "thread" in paragraphs[0].clean_text


def test_parse_fls_rst_preserves_role_aware_metadata(tmp_path: Path) -> None:
    rst_path = tmp_path / "glossary.rst"
    rst_path.write_text(
        """
Glossary
========

.. _fls_glossary001:

:dp:`fls_glossary001`
The :dt:`strict provenance` model constrains :t:`pointer` interpretation and :std:`core::ptr::addr_of` usage.
""".strip()
        + "\n",
        encoding="utf-8",
    )

    paragraphs = parse_fls_rst(
        rst_path,
        paragraph_numbers={"fls_glossary001": "3.1:2"},
        paragraph_metadata={
            "fls_glossary001": {
                "document_link": "glossary.html",
                "paragraph_link": "glossary.html#fls_glossary001",
                "section_link": "glossary.html#terms",
                "section_id": "terms",
                "checksum": "checksum-glossary",
            }
        },
    )

    assert len(paragraphs) == 1
    paragraph = paragraphs[0]
    assert paragraph.defined_terms == ("strict provenance",)
    assert paragraph.term_refs == ("pointer",)
    assert paragraph.std_refs == ("core::ptr::addr_of",)
    assert paragraph.document_link == "glossary.html"
    assert paragraph.paragraph_link == "glossary.html#fls_glossary001"
    assert paragraph.section_link == "glossary.html#terms"
    assert paragraph.section_id == "terms"
    assert paragraph.checksum == "checksum-glossary"


def test_parse_fls_rst_preserves_role_targets_and_bracketed_text(tmp_path: Path) -> None:
    rst_path = tmp_path / "ffi.rst"
    rst_path.write_text(
        """
Foreign Function Interface
==========================

.. _fls_ffi001:

:dp:`fls_ffi001`
The :dt:`external function <extern function>` calling convention constrains :t:`thread[s] <thread>` interaction and :p:`17.2 <fls_threads002>` semantics.
""".strip()
        + "\n",
        encoding="utf-8",
    )

    paragraphs = parse_fls_rst(
        rst_path,
        paragraph_numbers={"fls_ffi001": "21:4"},
        paragraph_metadata={
            "fls_ffi001": {
                "document_link": "ffi.html",
                "paragraph_link": "ffi.html#fls_ffi001",
                "section_link": "ffi.html#ffi",
                "section_id": "ffi",
                "checksum": "checksum-ffi",
            }
        },
    )

    assert len(paragraphs) == 1
    paragraph = paragraphs[0]
    assert paragraph.raw_text != paragraph.clean_text
    assert ":dt:`external function <extern function>`" in paragraph.raw_text
    assert paragraph.clean_text == (
        "The external function <extern function> calling convention constrains "
        "threads <thread> interaction and 17.2 <fls_threads002> semantics."
    )
    assert paragraph.defined_terms == ("external function",)
    assert paragraph.defined_term_targets == ("extern function",)
    assert paragraph.term_refs == ("threads",)
    assert paragraph.term_ref_targets == ("thread",)
    assert paragraph.paragraph_refs == ("17.2",)
    assert paragraph.paragraph_ref_targets == ("fls_threads002",)


def test_build_and_lookup_fls_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_dir = tmp_path / "fls_source"
    db_path = tmp_path / "fls_spec.db"
    spec_lock_path = tmp_path / "spec.lock"
    topology_path = tmp_path / "paragraph-ids.json"

    _write_sample_fls_source(source_dir)
    _write_spec_lock(spec_lock_path)
    _write_paragraph_ids(topology_path)
    monkeypatch.setattr("context.fls_lookup.TOPOLOGY_PATH", topology_path)
    monkeypatch.setattr("context.fls_lookup._TOPOLOGY_INDEX_CACHE", None)
    monkeypatch.setattr("context.fls_lookup._TOPOLOGY_DRIFT_CACHE", None)

    stats = build_fls_db(
        source_dir=source_dir,
        db_path=db_path,
        spec_lock_path=spec_lock_path,
        topology_path=topology_path,
        compat_symlink_mode="never",
    )
    assert stats["paragraph_count"] == 3
    assert stats["chapter_count"] == 2
    assert stats["commit_sha"] == "sample-sha"
    assert stats["document_count"] == 2
    assert stats["section_count"] == 2
    assert stats["retrieval_eligible_count"] == 3
    assert stats["audit_only_count"] == 0

    _seed_chunk_embeddings(db_path)

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        }
        assert {
            "paragraphs",
            "fls_documents",
            "fls_sections",
            "source_documents",
            "sections",
            "chunks",
            "chunk_spans",
            "chunk_embeddings",
            "semantic_models",
            "fls_paragraph_defined_terms",
            "fls_paragraph_term_refs",
            "fls_paragraph_syntax_defs",
            "fls_paragraph_syntax_refs",
            "fls_paragraph_std_refs",
            "fls_paragraph_refs",
        }.issubset(tables)
        doc_link, section_link = connection.execute(
            "SELECT document_link, section_link FROM paragraphs WHERE paragraph_id = ?",
            ("fls_atomic002",),
        ).fetchone()
        assert doc_link == "concurrency.html"
        assert section_link == "concurrency.html#fls_chapter_anchor"
        raw_text, clean_text = connection.execute(
            "SELECT raw_text, clean_text FROM paragraphs WHERE paragraph_id = ?",
            ("fls_sendsync001",),
        ).fetchone()
        assert ":t:`thread`" in raw_text
        assert ":t:`thread`" not in clean_text
        chunk_row = connection.execute(
            "SELECT chunk_uid, section_id FROM chunks WHERE chunk_uid = ?",
            ("fls_atomic002",),
        ).fetchone()
        assert chunk_row == ("fls_atomic002", "fls_chapter_anchor")
        span_row = connection.execute(
            "SELECT source_anchor, start_offset, span_order FROM chunk_spans WHERE chunk_uid = ?",
            ("fls_atomic002",),
        ).fetchone()
        assert span_row == ("concurrency.html#fls_atomic002", 0, 1)

    query_log_root = tmp_path / "query_logs"

    retrieval_result = execute_retrieval_query(
        mode="lexical",
        db_path=db_path,
        contract_path=Path("config/sqlite_query_contracts/fls_spec.yaml"),
        query_log_root=query_log_root,
        query_text="atomic fence ordering",
        row_marker="",
        top_k=5,
        candidate_limit=200,
        allow_degraded=True,
        semantic_config=SemanticBackendConfig(
            base_url="http://127.0.0.1:1",
            embed_model_id="Qwen/Qwen3-Embedding-4B",
            reranker_model_id="BAAI/bge-reranker-v2-m3",
            timeout_sec=0.2,
        ),
        semantic_retries=0,
        persist_semantic_cache=False,
        allow_online_corpus_embedding=False,
        corpus="fls_spec",
    )
    assert retrieval_result["rows"]
    assert retrieval_result["rows"][0]["chunk_uid"] == "fls_atomic002"
    assert retrieval_result["rows"][0]["paragraph_id"] == "fls_atomic002"

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT chunk_uid FROM chunks WHERE chunk_uid = ?",
            ("fls_atomic002",),
        ).fetchall() == [("fls_atomic002",)]
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM chunks c JOIN paragraphs p ON p.paragraph_id = c.chunk_uid WHERE c.clean_text != p.clean_text"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'statement_embeddings'"
            ).fetchone()[0]
            == 0
        )

    monkeypatch.setattr(
        "retrieval.operations.query.check_semantic_backend",
        lambda _config: {"ok": True, "checks": []},
    )
    monkeypatch.setattr(
        "scripts.retrieval.operations.query.check_semantic_backend",
        lambda _config: {"ok": True, "checks": []},
    )
    monkeypatch.setattr(
        "retrieval.query.semantic_pipeline.embed_texts",
        lambda _config, texts: [[0.1, 0.2, 0.3] for _ in texts],
    )
    monkeypatch.setattr(
        "scripts.retrieval.query.semantic_pipeline.embed_texts",
        lambda _config, texts: [[0.1, 0.2, 0.3] for _ in texts],
    )
    monkeypatch.setattr(
        "retrieval.query.semantic_pipeline.rerank_texts",
        lambda config, query_text, documents: [
            1.0 - (0.05 * idx) for idx, _ in enumerate(documents)
        ],
    )
    monkeypatch.setattr(
        "scripts.retrieval.query.semantic_pipeline.rerank_texts",
        lambda config, query_text, documents: [
            1.0 - (0.05 * idx) for idx, _ in enumerate(documents)
        ],
    )
    monkeypatch.setattr(
        "semantic_backend_client.rerank_texts",
        lambda config, query_text, documents: [
            1.0 - (0.05 * idx) for idx, _ in enumerate(documents)
        ],
    )
    monkeypatch.setattr(
        "scripts.semantic_backend_client.rerank_texts",
        lambda config, query_text, documents: [
            1.0 - (0.05 * idx) for idx, _ in enumerate(documents)
        ],
    )

    semantic_result = execute_retrieval_query(
        mode="semantic",
        db_path=db_path,
        contract_path=Path("config/sqlite_query_contracts/fls_spec.yaml"),
        query_log_root=query_log_root,
        query_text="atomic fence ordering",
        row_marker="",
        top_k=5,
        candidate_limit=200,
        allow_degraded=False,
        semantic_config=SemanticBackendConfig(
            base_url="http://127.0.0.1:8080",
            embed_base_url="http://127.0.0.1:8080",
            rerank_base_url="http://127.0.0.1:8081",
            embed_model_id="Qwen/Qwen3-Embedding-4B",
            reranker_model_id="BAAI/bge-reranker-v2-m3",
            timeout_sec=0.2,
        ),
        semantic_retries=0,
        persist_semantic_cache=False,
        allow_online_corpus_embedding=False,
        corpus="fls_spec",
        rewrite_rules_path=Path("config/sqlite_query_rewrite/fls_spec_rewrite.yaml"),
        hybrid_fusion_method="weighted-v2",
        hybrid_candidate_policy="v2",
        hybrid_rerank_pool_size=128,
        hybrid_lexical_min=24,
        hybrid_semantic_min=24,
        hybrid_lexical_floor_count=24,
        hybrid_lexical_floor_share=0.25,
        hybrid_rrf_k=60,
        hybrid_rrf_window=128,
    )
    hybrid_result = execute_retrieval_query(
        mode="hybrid",
        db_path=db_path,
        contract_path=Path("config/sqlite_query_contracts/fls_spec.yaml"),
        query_log_root=query_log_root,
        query_text="atomic fence ordering",
        row_marker="",
        top_k=5,
        candidate_limit=200,
        allow_degraded=False,
        semantic_config=SemanticBackendConfig(
            base_url="http://127.0.0.1:8080",
            embed_base_url="http://127.0.0.1:8080",
            rerank_base_url="http://127.0.0.1:8081",
            embed_model_id="Qwen/Qwen3-Embedding-4B",
            reranker_model_id="BAAI/bge-reranker-v2-m3",
            timeout_sec=0.2,
        ),
        semantic_retries=0,
        persist_semantic_cache=False,
        allow_online_corpus_embedding=False,
        corpus="fls_spec",
        rewrite_rules_path=Path("config/sqlite_query_rewrite/fls_spec_rewrite.yaml"),
        hybrid_fusion_method="weighted-v2",
        hybrid_candidate_policy="v2",
        hybrid_rerank_pool_size=128,
        hybrid_lexical_min=24,
        hybrid_semantic_min=24,
        hybrid_lexical_floor_count=24,
        hybrid_lexical_floor_share=0.25,
        hybrid_rrf_k=60,
        hybrid_rrf_window=128,
    )
    assert semantic_result["rows"][0]["chunk_uid"] == "fls_atomic002"
    assert hybrid_result["rows"][0]["chunk_uid"] == "fls_atomic002"
    assert semantic_result["rows"][0]["term_refs_json"]
    assert hybrid_result["rows"][0]["term_refs_json"]

    alt_contract_path = tmp_path / "fls_spec_alt_contract.yaml"
    alt_contract_path.write_text(
        Path("config/sqlite_query_contracts/fls_spec.yaml")
        .read_text(encoding="utf-8")
        .replace("'' AS row_markers", "'marker-x' AS row_markers")
        .replace("'' AS mechanism_ids", "'mechanism-x' AS mechanism_ids")
        .replace("'' AS mechanism_families", "'family-x' AS mechanism_families"),
        encoding="utf-8",
    )
    lexical_modified = execute_retrieval_query(
        mode="lexical",
        db_path=db_path,
        contract_path=alt_contract_path,
        query_log_root=query_log_root,
        query_text="atomic fence ordering",
        row_marker="",
        top_k=5,
        candidate_limit=200,
        allow_degraded=True,
        semantic_config=SemanticBackendConfig(
            base_url="http://127.0.0.1:1",
            embed_model_id="Qwen/Qwen3-Embedding-4B",
            reranker_model_id="BAAI/bge-reranker-v2-m3",
            timeout_sec=0.2,
        ),
        semantic_retries=0,
        persist_semantic_cache=False,
        allow_online_corpus_embedding=False,
        corpus="fls_spec",
    )
    assert [row["chunk_uid"] for row in lexical_modified["rows"]] == [
        row["chunk_uid"] for row in retrieval_result["rows"]
    ]
    assert [row["relevance_score"] for row in lexical_modified["rows"]] == [
        row["relevance_score"] for row in retrieval_result["rows"]
    ]

    search_results = search_fls_paragraphs("atomic fence ordering", db_path=db_path)
    assert search_results
    assert search_results[0]["chapter"] == "Concurrency"
    assert set(search_results[0]["retrieved_modes"]) == {"lexical", "semantic", "hybrid"}

    logged_query_ids: set[str] = set()
    for path in query_log_root.glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            logged_query_ids.add(str(payload.get("query_id", "")))
    assert {
        "chunk_corpus_v1_all",
        "lexical_chunk_search_v1",
        "table1_row_requirements_v2",
    }.issubset(logged_query_ids)

    resolved = resolve_fls_for_construct(
        ["atomic", "fence", "ordering"],
        db_path=db_path,
        spec_lock_path=spec_lock_path,
    )
    assert resolved["paragraph_id"] == "fls_UNRESOLVED"
    assert resolved["decision"]["reason_code"] == "WS7_REQUIRED"

    resolved_with_domains = resolve_fls_for_construct(
        ["atomic", "fence", "ordering"],
        db_path=db_path,
        spec_lock_path=spec_lock_path,
        expected_domains=["unsafe", "concurrency"],
    )
    assert resolved_with_domains == resolved

    assert validate_fls_id("fls_atomic002", spec_lock_path=spec_lock_path)
    assert not validate_fls_id("fls_FABRICATED_ID", spec_lock_path=spec_lock_path)

    membership = get_live_topology_membership(paragraph_id="fls_atomic002")
    assert membership is not None
    assert membership["document_link"] == "concurrency.html"
    assert membership["section_link"] == "concurrency.html#fls_chapter_anchor"


def test_build_fls_db_never_mode_does_not_mutate_repo_compat_symlink(tmp_path: Path) -> None:
    source_dir = tmp_path / "fls_source"
    db_path = tmp_path / "fls_spec.db"
    spec_lock_path = tmp_path / "spec.lock"
    topology_path = tmp_path / "paragraph-ids.json"

    _write_sample_fls_source(source_dir)
    _write_spec_lock(spec_lock_path)
    _write_paragraph_ids(topology_path)

    compat_path = Path("data/fls_spec.db")
    before_target = compat_path.readlink() if compat_path.is_symlink() else None

    build_fls_db(
        source_dir=source_dir,
        db_path=db_path,
        spec_lock_path=spec_lock_path,
        topology_path=topology_path,
        compat_symlink_mode="never",
    )

    after_target = compat_path.readlink() if compat_path.is_symlink() else None
    assert after_target == before_target


def test_build_fls_db_auto_mode_updates_compat_for_canonical_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_dir = tmp_path / "fls_source"
    canonical_db = tmp_path / "canonical" / "fls_spec.db"
    compat_link = tmp_path / "compat" / "fls_spec.db"
    spec_lock_path = tmp_path / "spec.lock"
    topology_path = tmp_path / "paragraph-ids.json"

    _write_sample_fls_source(source_dir)
    _write_spec_lock(spec_lock_path)
    _write_paragraph_ids(topology_path)

    monkeypatch.setattr("scripts.build_fls_db.DB_PATH", canonical_db)
    monkeypatch.setattr("scripts.build_fls_db.COMPAT_DB_PATH", compat_link)

    build_fls_db(
        source_dir=source_dir,
        db_path=canonical_db,
        spec_lock_path=spec_lock_path,
        topology_path=topology_path,
        compat_symlink_mode="auto",
    )

    assert compat_link.is_symlink()
    assert compat_link.resolve() == canonical_db.resolve()


def test_build_fls_db_always_mode_updates_compat_for_noncanonical_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_dir = tmp_path / "fls_source"
    noncanonical_db = tmp_path / "noncanonical" / "fls_spec.db"
    canonical_db = tmp_path / "canonical" / "fls_spec.db"
    compat_link = tmp_path / "compat" / "fls_spec.db"
    spec_lock_path = tmp_path / "spec.lock"
    topology_path = tmp_path / "paragraph-ids.json"

    _write_sample_fls_source(source_dir)
    _write_spec_lock(spec_lock_path)
    _write_paragraph_ids(topology_path)

    monkeypatch.setattr("scripts.build_fls_db.DB_PATH", canonical_db)
    monkeypatch.setattr("scripts.build_fls_db.COMPAT_DB_PATH", compat_link)

    build_fls_db(
        source_dir=source_dir,
        db_path=noncanonical_db,
        spec_lock_path=spec_lock_path,
        topology_path=topology_path,
        compat_symlink_mode="always",
    )

    assert compat_link.is_symlink()
    assert compat_link.resolve() == noncanonical_db.resolve()


def test_repo_compat_symlink_not_pointing_to_pytest_temp() -> None:
    compat_path = Path("data/fls_spec.db")
    if not compat_path.is_symlink():
        pytest.skip("compat symlink not present in this environment")

    target = str(compat_path.readlink())
    assert "pytest-" not in target
    assert "/private/var/folders/" not in target


def test_resolve_raises_when_db_missing(tmp_path: Path) -> None:
    spec_lock_path = tmp_path / "spec.lock"
    _write_spec_lock(spec_lock_path)

    with pytest.raises(RuntimeError, match="FLS DB unavailable or empty"):
        resolve_fls_for_construct(
            ["Send", "Sync"],
            db_path=tmp_path / "missing.db",
            spec_lock_path=spec_lock_path,
        )


def test_fetch_fls_source_hard_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def always_fail(*, target_ref: str, output_dir: Path, branch: str):
        raise RuntimeError("network down")

    monkeypatch.setattr("scripts.fetch_fls_source._fetch_once", always_fail)
    monkeypatch.setattr("scripts.fetch_fls_source.time.sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="FLS_FETCH_FAILURE"):
        fetch_fls_source(
            output_dir=tmp_path / "fls_source",
            max_retries=3,
            backoff_base_seconds=0,
        )


def test_build_fls_db_persists_role_targets(tmp_path: Path) -> None:
    source_dir = tmp_path / "fls_source"
    db_path = tmp_path / "fls_spec.db"
    spec_lock_path = tmp_path / "spec.lock"
    topology_path = tmp_path / "paragraph-ids.json"

    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "ffi.rst").write_text(
        """
Foreign Function Interface
==========================

.. _fls_ffi001:

:dp:`fls_ffi001`
The :dt:`external function <extern function>` calling convention constrains :t:`thread[s] <thread>` interaction and :p:`17.2 <fls_threads002>` semantics.
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (source_dir / "_metadata.json").write_text(
        json.dumps({"commit_sha": "sample-sha"}),
        encoding="utf-8",
    )
    spec_lock_path.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "title": "Foreign Function Interface",
                        "sections": [
                            {
                                "id": "fls_ffi_anchor",
                                "paragraphs": [{"id": "fls_ffi001", "number": "21:4"}],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    topology_path.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "title": "Foreign Function Interface",
                        "link": "ffi.html",
                        "informational": False,
                        "sections": [
                            {
                                "id": "fls_ffi_anchor",
                                "number": "21",
                                "title": "Foreign Function Interface",
                                "link": "ffi.html",
                                "informational": False,
                                "paragraphs": [
                                    {
                                        "id": "fls_ffi001",
                                        "number": "21:4",
                                        "link": "ffi.html#fls_ffi001",
                                        "checksum": "checksum-ffi",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    build_fls_db(
        source_dir=source_dir,
        db_path=db_path,
        spec_lock_path=spec_lock_path,
        topology_path=topology_path,
        compat_symlink_mode="never",
    )

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT term_text, term_target FROM fls_paragraph_defined_terms"
        ).fetchall() == [("external function", "extern function")]
        assert connection.execute(
            "SELECT term_text, term_target FROM fls_paragraph_term_refs"
        ).fetchall() == [("threads", "thread")]
        assert connection.execute(
            "SELECT ref_text, ref_target FROM fls_paragraph_refs"
        ).fetchall() == [("17.2", "fls_threads002")]
