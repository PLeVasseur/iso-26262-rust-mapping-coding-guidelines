from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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

            for bib in list(record.get("bibliography_rows") or []):
                citation_key_raw = str(bib.get("citation_key", "")).strip()
                citation_key = re.sub(r"[^A-Z0-9-]", "-", citation_key_raw.upper()).strip("-")
                if not citation_key:
                    continue
                url = str(bib.get("url", "")).strip()
                title_text = str(bib.get("title", "")).strip() or citation_key
                author = str(bib.get("publisher", "")).strip() or "Reference"
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
