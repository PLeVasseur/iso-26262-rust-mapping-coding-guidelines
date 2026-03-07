from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _bibliography_url(row: dict[str, Any]) -> str:
    for key in ("url", "source_anchor"):
        value = str(row.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _bibliography_author(row: dict[str, Any]) -> str:
    for key in ("author", "publisher", "document", "corpus"):
        value = str(row.get(key, "") or "").strip()
        if value:
            return value
    return "Reference"


def _bibliography_title(row: dict[str, Any], *, fallback: str) -> str:
    return str(row.get("title", "") or "").strip().rstrip(".") or fallback


def _canonicalize_bibliography_rows(
    rows: list[dict[str, Any]],
    *,
    canonical_by_url: dict[str, tuple[str, str, str]] | None = None,
) -> list[dict[str, Any]]:
    effective_canonical = canonical_by_url if canonical_by_url is not None else {}
    out: list[dict[str, Any]] = []
    for row in rows:
        citation_key = str(row.get("citation_key", "") or "").strip()
        if not citation_key:
            continue
        title = _bibliography_title(row, fallback=citation_key)
        author = _bibliography_author(row)
        url = _bibliography_url(row)
        if url:
            canonical_key, canonical_author, canonical_title = effective_canonical.get(
                url, (citation_key, author, title)
            )
            effective_canonical.setdefault(url, (canonical_key, canonical_author, canonical_title))
            citation_key = canonical_key
            author = canonical_author
            title = canonical_title
        out.append(
            {
                **row,
                "citation_key": citation_key,
                "author": author,
                "title": title,
                "url": url,
            }
        )
    return out


def _batch_canonical_by_url(records: list[dict[str, Any]]) -> dict[str, tuple[str, str, str]]:
    canonical_by_url: dict[str, tuple[str, str, str]] = {}
    for record in records:
        for bib in list(record.get("bibliography_rows") or []):
            if not isinstance(bib, dict):
                continue
            citation_key = str(bib.get("citation_key", "") or "").strip() or "REFERENCE"
            url = _bibliography_url(bib)
            if not url or url in canonical_by_url:
                continue
            canonical_by_url[url] = (
                citation_key,
                _bibliography_author(bib),
                _bibliography_title(bib, fallback=citation_key),
            )
    return canonical_by_url


def _init_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS guideline_records(
            guideline_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source_file_path TEXT NOT NULL,
            quality_label TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            export_topic TEXT NOT NULL,
            source_revision TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            ingested_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS guideline_blocks(
            block_id TEXT PRIMARY KEY,
            guideline_id TEXT NOT NULL,
            block_type TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            content TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS guideline_bibliography(
            bib_key TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            source_file_path TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS guideline_bib_links(
            guideline_id TEXT NOT NULL,
            bib_key TEXT NOT NULL,
            PRIMARY KEY(guideline_id, bib_key)
        );
        """
    )


def ingest_records(
    *, db_path: Path, records: list[dict[str, Any]], source_run_id: str
) -> dict[str, Any]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        _init_schema(connection)
        canonical_by_url = _batch_canonical_by_url(records)
        for record in records:
            guideline_id = str(record["guideline_id"])
            chapter = str(record["chapter"])
            filename = str(record["filename"])
            title = str(record["title"])
            metadata_json = json.dumps(
                {
                    "export_filename": filename,
                    "source_run_id": source_run_id,
                    "target_id": record.get("target_id", ""),
                    "category": record.get("category", "advisory"),
                    "status": record.get("status", "draft"),
                    "release": record.get("release", "1.85.1"),
                    "fls_id": record.get("fls_id", ""),
                    "fls_resolution": dict(record.get("fls_resolution") or {}),
                    "fls_resolution_report": str(record.get("fls_resolution_report", "")),
                    "publishability": dict(record.get("publishability") or {}),
                    "decidability": record.get("decidability", "undecidable"),
                    "scope": record.get("scope", "module"),
                    "tags": list(record.get("tags") or []),
                    "non_compliant_miri_intent": record.get("non_compliant_miri_intent", ""),
                    "compliant_miri_intent": record.get("compliant_miri_intent", ""),
                    "non_compliant_miri_skip_justification": record.get(
                        "non_compliant_miri_skip_justification", ""
                    ),
                    "compliant_miri_skip_justification": record.get(
                        "compliant_miri_skip_justification", ""
                    ),
                },
                sort_keys=False,
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO guideline_records(
                    guideline_id, title, source_file_path, quality_label,
                    metadata_json, export_topic, source_revision, source_hash, ingested_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guideline_id,
                    title,
                    f"src/coding-guidelines/{chapter}/{filename}",
                    "draft",
                    metadata_json,
                    chapter,
                    source_run_id,
                    "writer-artifacts",
                    _now(),
                ),
            )

            for block in list(record.get("blocks") or []):
                block_type = str(block.get("block_type", "")).strip()
                content = str(block.get("content", "")).strip()
                order_index = int(block.get("order_index", 0) or 0)
                if not block_type or not content:
                    continue
                block_id = f"{guideline_id}:{block_type}:{order_index}"
                connection.execute(
                    (
                        "INSERT OR REPLACE INTO guideline_blocks("
                        "block_id, guideline_id, block_type, order_index, content"
                        ") VALUES(?, ?, ?, ?, ?)"
                    ),
                    (block_id, guideline_id, block_type, order_index, content),
                )

            bibliography_rows = _canonicalize_bibliography_rows(
                [
                    bib
                    for bib in list(record.get("bibliography_rows") or [])
                    if isinstance(bib, dict)
                ],
                canonical_by_url=canonical_by_url,
            )
            for bib in bibliography_rows:
                citation_key_raw = str(bib.get("citation_key", "")).strip()
                citation_key = re.sub(r"[^A-Z0-9-]", "-", citation_key_raw.upper()).strip("-")
                if not citation_key:
                    continue
                url = _bibliography_url(bib)
                title_text = _bibliography_title(bib, fallback=citation_key)
                author = _bibliography_author(bib)
                content = json.dumps(
                    {
                        "citation_key": citation_key,
                        "author": author,
                        "title": title_text,
                        "url": url,
                    },
                    sort_keys=False,
                )
                connection.execute(
                    (
                        "INSERT OR REPLACE INTO guideline_bibliography("
                        "bib_key, content, source_file_path"
                        ") VALUES(?, ?, ?)"
                    ),
                    (citation_key, content, f"writer/{source_run_id}"),
                )
                connection.execute(
                    (
                        "INSERT OR REPLACE INTO guideline_bib_links("
                        "guideline_id, bib_key"
                        ") VALUES(?, ?)"
                    ),
                    (guideline_id, citation_key),
                )

        connection.commit()
    finally:
        connection.close()

    return {
        "db_path": str(db_path),
        "record_count": len(records),
    }
