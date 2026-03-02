"""Build `data/fls_spec.db` from parsed FLS RST paragraph sources."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

try:
    from scripts.parse_fls_paragraphs import (
        DEFAULT_SPEC_LOCK_PATH,
        load_paragraph_numbers,
        parse_all_fls,
    )
except ModuleNotFoundError:  # pragma: no cover - script-entry fallback
    from parse_fls_paragraphs import DEFAULT_SPEC_LOCK_PATH, load_paragraph_numbers, parse_all_fls

FLS_SOURCE_DIR = Path("data/fls_source")
DB_PATH = Path("data/fls_spec.db")


def _load_commit_sha(source_dir: Path) -> str:
    metadata_path = source_dir / "_metadata.json"
    if not metadata_path.exists():
        return "local"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "local"
    return str(metadata.get("commit_sha") or "local")


def build_fls_db(
    source_dir: Path = FLS_SOURCE_DIR,
    db_path: Path = DB_PATH,
    spec_lock_path: Path = DEFAULT_SPEC_LOCK_PATH,
) -> dict[str, Any]:
    paragraph_numbers = load_paragraph_numbers(spec_lock_path=spec_lock_path)
    paragraphs = parse_all_fls(source_dir, paragraph_numbers=paragraph_numbers)
    if not paragraphs:
        raise RuntimeError(
            f"No FLS paragraphs parsed from {source_dir}. Cannot create a stub fls_spec DB."
        )

    commit_sha = _load_commit_sha(source_dir)
    chapters = sorted({paragraph.chapter for paragraph in paragraphs if paragraph.chapter})

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    connection = sqlite3.connect(str(db_path))
    try:
        connection.executescript(
            """
            CREATE TABLE snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                commit_sha TEXT NOT NULL,
                built_at TEXT NOT NULL DEFAULT (datetime('now')),
                paragraph_count INTEGER NOT NULL,
                chapter_count INTEGER NOT NULL
            );

            CREATE TABLE paragraphs (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                paragraph_id TEXT NOT NULL UNIQUE,
                paragraph_number TEXT NOT NULL,
                chapter TEXT NOT NULL,
                section TEXT NOT NULL DEFAULT '',
                subsection TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                source_file TEXT NOT NULL,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id)
            );

            CREATE VIRTUAL TABLE paragraphs_fts USING fts5(
                paragraph_id,
                paragraph_number,
                chapter,
                section,
                text,
                content='paragraphs',
                content_rowid='rowid'
            );

            CREATE TRIGGER paragraphs_ai AFTER INSERT ON paragraphs
            BEGIN
                INSERT INTO paragraphs_fts(
                    rowid,
                    paragraph_id,
                    paragraph_number,
                    chapter,
                    section,
                    text
                ) VALUES (
                    new.rowid,
                    new.paragraph_id,
                    new.paragraph_number,
                    new.chapter,
                    new.section,
                    new.text
                );
            END;

            CREATE INDEX idx_paragraphs_chapter ON paragraphs(chapter);
            CREATE INDEX idx_paragraphs_section ON paragraphs(section);
            """
        )

        cursor = connection.execute(
            """
            INSERT INTO snapshots(commit_sha, paragraph_count, chapter_count)
            VALUES(?, ?, ?)
            """,
            (commit_sha, len(paragraphs), len(chapters)),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Failed to create FLS snapshot row")
        snapshot_id = int(cursor.lastrowid)

        for paragraph in paragraphs:
            connection.execute(
                """
                INSERT INTO paragraphs(
                    paragraph_id,
                    paragraph_number,
                    chapter,
                    section,
                    subsection,
                    text,
                    source_file,
                    snapshot_id
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    paragraph.paragraph_id,
                    paragraph.paragraph_number,
                    paragraph.chapter,
                    paragraph.section,
                    paragraph.subsection,
                    paragraph.text,
                    paragraph.source_file,
                    snapshot_id,
                ),
            )

        connection.commit()
    finally:
        connection.close()

    return {
        "db_path": str(db_path),
        "commit_sha": commit_sha,
        "paragraph_count": len(paragraphs),
        "chapter_count": len(chapters),
        "chapters": chapters,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=FLS_SOURCE_DIR)
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--spec-lock-path", type=Path, default=DEFAULT_SPEC_LOCK_PATH)
    args = parser.parse_args()

    stats = build_fls_db(
        source_dir=args.source_dir,
        db_path=args.db_path,
        spec_lock_path=args.spec_lock_path,
    )
    print(
        f"FLS DB built: {stats['paragraph_count']} paragraphs from "
        f"{stats['chapter_count']} chapters"
    )
    print(f"Commit: {stats['commit_sha']}")


if __name__ == "__main__":
    main()
