from __future__ import annotations

import json
from pathlib import Path

import pytest
from context.fls_lookup import resolve_fls_for_construct, search_fls_paragraphs, validate_fls_id
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


def test_build_and_lookup_fls_db(tmp_path: Path) -> None:
    source_dir = tmp_path / "fls_source"
    db_path = tmp_path / "fls_spec.db"
    spec_lock_path = tmp_path / "spec.lock"

    _write_sample_fls_source(source_dir)
    _write_spec_lock(spec_lock_path)

    stats = build_fls_db(source_dir=source_dir, db_path=db_path, spec_lock_path=spec_lock_path)
    assert stats["paragraph_count"] == 3
    assert stats["chapter_count"] == 2
    assert stats["commit_sha"] == "sample-sha"

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
