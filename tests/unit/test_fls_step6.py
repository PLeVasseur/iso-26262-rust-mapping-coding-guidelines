from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from context.fls_lookup import (
    get_live_topology_membership,
    resolve_fls_for_construct,
    search_fls_paragraphs,
    validate_fls_id,
)
from scripts.build_fls_db import build_fls_db
from scripts.fetch_fls_source import fetch_fls_source
from scripts.parse_fls_paragraphs import parse_fls_rst


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
    assert "thread" in paragraphs[0].text


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

    search_results = search_fls_paragraphs("atomic fence ordering", db_path=db_path)
    assert search_results
    assert search_results[0]["chapter"] == "Concurrency"

    resolved = resolve_fls_for_construct(
        ["atomic", "fence", "ordering"],
        db_path=db_path,
        spec_lock_path=spec_lock_path,
    )
    assert resolved["paragraph_id"] == "fls_atomic002"
    assert resolved["paragraph_number"].startswith("17")

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
