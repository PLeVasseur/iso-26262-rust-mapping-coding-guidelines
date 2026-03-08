"""Build `fls_spec` SQLite DB from parsed FLS RST paragraph sources."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Literal

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from context.fls_topology import DEFAULT_TOPOLOGY_CACHE_PATH, load_topology_index

try:
    from scripts.parse_fls_paragraphs import (
        DEFAULT_SPEC_LOCK_PATH,
        DEFAULT_TOPOLOGY_PATH,
        load_paragraph_numbers,
        parse_all_fls,
    )
except ModuleNotFoundError:  # pragma: no cover - script-entry fallback
    from parse_fls_paragraphs import (
        DEFAULT_SPEC_LOCK_PATH,
        DEFAULT_TOPOLOGY_PATH,
        load_paragraph_numbers,
        parse_all_fls,
    )

FLS_SOURCE_DIR = Path(".cache/fls_source/current")
DB_PATH = Path(".cache/sqlite_kb/current/fls_spec.db")
COMPAT_DB_PATH = Path("data/fls_spec.db")


def _ensure_compat_symlink(canonical_db_path: Path, compat_db_path: Path | None = None) -> None:
    if compat_db_path is None:
        compat_db_path = COMPAT_DB_PATH
    compat_db_path.parent.mkdir(parents=True, exist_ok=True)
    if compat_db_path.exists() or compat_db_path.is_symlink():
        compat_db_path.unlink()
    rel_target = Path(os.path.relpath(canonical_db_path, compat_db_path.parent))
    compat_db_path.symlink_to(rel_target)


def _should_update_compat_symlink(
    *,
    db_path: Path,
    compat_symlink_mode: Literal["auto", "always", "never"],
) -> bool:
    if compat_symlink_mode == "always":
        return True
    if compat_symlink_mode == "never":
        return False
    return db_path.resolve() == DB_PATH.resolve()


def _load_commit_sha(source_dir: Path) -> str:
    metadata_path = source_dir / "_metadata.json"
    if not metadata_path.exists():
        return "local"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "local"
    return str(metadata.get("commit_sha") or "local")


def _insert_ordered_text_rows(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    value_column: str,
    order_column: str,
    paragraph_id: str,
    values: tuple[str, ...],
) -> None:
    for ordinal, value in enumerate(values):
        connection.execute(
            f"""
            INSERT INTO {table_name}(paragraph_id, {value_column}, {order_column})
            VALUES(?, ?, ?)
            """,
            (paragraph_id, value, ordinal),
        )


def build_fls_db(
    source_dir: Path = FLS_SOURCE_DIR,
    db_path: Path = DB_PATH,
    spec_lock_path: Path = DEFAULT_SPEC_LOCK_PATH,
    topology_path: Path = DEFAULT_TOPOLOGY_PATH,
    compat_symlink_mode: Literal["auto", "always", "never"] = "auto",
) -> dict[str, Any]:
    paragraph_numbers = load_paragraph_numbers(spec_lock_path=spec_lock_path)
    resolved_topology_path = topology_path or DEFAULT_TOPOLOGY_CACHE_PATH
    if not resolved_topology_path.exists():
        raise RuntimeError(
            f"FLS topology not found at {resolved_topology_path}. Rebuild requires paragraph-ids.json."
        )
    topology_index = load_topology_index(topology_path=resolved_topology_path)
    paragraphs = parse_all_fls(
        source_dir,
        paragraph_numbers=paragraph_numbers,
        spec_lock_path=spec_lock_path,
        topology_path=resolved_topology_path,
    )
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
                chapter_count INTEGER NOT NULL,
                document_count INTEGER NOT NULL,
                section_count INTEGER NOT NULL
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
                document_link TEXT NOT NULL,
                paragraph_link TEXT NOT NULL,
                section_link TEXT NOT NULL,
                section_id TEXT NOT NULL DEFAULT '',
                checksum TEXT NOT NULL DEFAULT '',
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id)
            );

            CREATE TABLE fls_documents (
                document_link TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                informational INTEGER NOT NULL
            );

            CREATE TABLE fls_sections (
                section_link TEXT PRIMARY KEY,
                section_id TEXT NOT NULL,
                document_link TEXT NOT NULL,
                title TEXT NOT NULL,
                number TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                informational INTEGER NOT NULL
            );

            CREATE TABLE fls_paragraph_defined_terms (
                paragraph_id TEXT NOT NULL,
                term_text TEXT NOT NULL,
                term_order INTEGER NOT NULL
            );

            CREATE TABLE fls_paragraph_term_refs (
                paragraph_id TEXT NOT NULL,
                term_text TEXT NOT NULL,
                term_order INTEGER NOT NULL
            );

            CREATE TABLE fls_paragraph_syntax_defs (
                paragraph_id TEXT NOT NULL,
                symbol_text TEXT NOT NULL,
                symbol_order INTEGER NOT NULL
            );

            CREATE TABLE fls_paragraph_syntax_refs (
                paragraph_id TEXT NOT NULL,
                symbol_text TEXT NOT NULL,
                symbol_order INTEGER NOT NULL
            );

            CREATE TABLE fls_paragraph_std_refs (
                paragraph_id TEXT NOT NULL,
                symbol_text TEXT NOT NULL,
                symbol_order INTEGER NOT NULL
            );

            CREATE TABLE fls_paragraph_refs (
                paragraph_id TEXT NOT NULL,
                ref_target TEXT NOT NULL,
                ref_order INTEGER NOT NULL
            );

            CREATE VIRTUAL TABLE paragraphs_fts USING fts5(
                paragraph_id,
                paragraph_number,
                chapter,
                section,
                subsection,
                document_link,
                section_link,
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
                    subsection,
                    document_link,
                    section_link,
                    text
                ) VALUES (
                    new.rowid,
                    new.paragraph_id,
                    new.paragraph_number,
                    new.chapter,
                    new.section,
                    new.subsection,
                    new.document_link,
                    new.section_link,
                    new.text
                );
            END;

            CREATE INDEX idx_paragraphs_chapter ON paragraphs(chapter);
            CREATE INDEX idx_paragraphs_section ON paragraphs(section);
            CREATE INDEX idx_paragraphs_document_link ON paragraphs(document_link);
            CREATE INDEX idx_paragraphs_section_link ON paragraphs(section_link);
            CREATE INDEX idx_defined_terms_paragraph_id ON fls_paragraph_defined_terms(paragraph_id);
            CREATE INDEX idx_term_refs_paragraph_id ON fls_paragraph_term_refs(paragraph_id);
            CREATE INDEX idx_syntax_defs_paragraph_id ON fls_paragraph_syntax_defs(paragraph_id);
            CREATE INDEX idx_syntax_refs_paragraph_id ON fls_paragraph_syntax_refs(paragraph_id);
            CREATE INDEX idx_std_refs_paragraph_id ON fls_paragraph_std_refs(paragraph_id);
            CREATE INDEX idx_paragraph_refs_paragraph_id ON fls_paragraph_refs(paragraph_id);
            """
        )

        cursor = connection.execute(
            """
            INSERT INTO snapshots(commit_sha, paragraph_count, chapter_count, document_count, section_count)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                commit_sha,
                len(paragraphs),
                len(chapters),
                len(topology_index["documents_by_link"]),
                len(topology_index["sections_by_link"]),
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Failed to create FLS snapshot row")
        snapshot_id = int(cursor.lastrowid)

        for document in topology_index["documents_by_link"].values():
            connection.execute(
                """
                INSERT INTO fls_documents(document_link, title, ordinal, informational)
                VALUES(?, ?, ?, ?)
                """,
                (
                    document.document_link,
                    document.title,
                    document.ordinal,
                    int(document.informational),
                ),
            )

        for section in topology_index["sections_by_link"].values():
            connection.execute(
                """
                INSERT INTO fls_sections(
                    section_link,
                    section_id,
                    document_link,
                    title,
                    number,
                    ordinal,
                    informational
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    section.section_link,
                    section.section_id,
                    section.document_link,
                    section.title,
                    section.number,
                    section.ordinal,
                    int(section.informational),
                ),
            )

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
                    document_link,
                    paragraph_link,
                    section_link,
                    section_id,
                    checksum,
                    snapshot_id
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    paragraph.paragraph_id,
                    paragraph.paragraph_number,
                    paragraph.chapter,
                    paragraph.section,
                    paragraph.subsection,
                    paragraph.text,
                    paragraph.source_file,
                    paragraph.document_link,
                    paragraph.paragraph_link,
                    paragraph.section_link,
                    paragraph.section_id,
                    paragraph.checksum,
                    snapshot_id,
                ),
            )
            _insert_ordered_text_rows(
                connection,
                table_name="fls_paragraph_defined_terms",
                value_column="term_text",
                order_column="term_order",
                paragraph_id=paragraph.paragraph_id,
                values=paragraph.defined_terms,
            )
            _insert_ordered_text_rows(
                connection,
                table_name="fls_paragraph_term_refs",
                value_column="term_text",
                order_column="term_order",
                paragraph_id=paragraph.paragraph_id,
                values=paragraph.term_refs,
            )
            _insert_ordered_text_rows(
                connection,
                table_name="fls_paragraph_syntax_defs",
                value_column="symbol_text",
                order_column="symbol_order",
                paragraph_id=paragraph.paragraph_id,
                values=paragraph.syntax_defs,
            )
            _insert_ordered_text_rows(
                connection,
                table_name="fls_paragraph_syntax_refs",
                value_column="symbol_text",
                order_column="symbol_order",
                paragraph_id=paragraph.paragraph_id,
                values=paragraph.syntax_refs,
            )
            _insert_ordered_text_rows(
                connection,
                table_name="fls_paragraph_std_refs",
                value_column="symbol_text",
                order_column="symbol_order",
                paragraph_id=paragraph.paragraph_id,
                values=paragraph.std_refs,
            )
            _insert_ordered_text_rows(
                connection,
                table_name="fls_paragraph_refs",
                value_column="ref_target",
                order_column="ref_order",
                paragraph_id=paragraph.paragraph_id,
                values=paragraph.paragraph_refs,
            )

        connection.commit()
    finally:
        connection.close()

    if _should_update_compat_symlink(db_path=db_path, compat_symlink_mode=compat_symlink_mode):
        _ensure_compat_symlink(db_path)

    return {
        "db_path": str(db_path),
        "commit_sha": commit_sha,
        "paragraph_count": len(paragraphs),
        "chapter_count": len(chapters),
        "document_count": len(topology_index["documents_by_link"]),
        "section_count": len(topology_index["sections_by_link"]),
        "chapters": chapters,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=FLS_SOURCE_DIR)
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--spec-lock-path", type=Path, default=DEFAULT_SPEC_LOCK_PATH)
    parser.add_argument("--topology-path", type=Path, default=DEFAULT_TOPOLOGY_PATH)
    parser.add_argument(
        "--compat-symlink-mode",
        choices=["auto", "always", "never"],
        default="auto",
        help="When to update data/fls_spec.db compat symlink",
    )
    args = parser.parse_args()

    stats = build_fls_db(
        source_dir=args.source_dir,
        db_path=args.db_path,
        spec_lock_path=args.spec_lock_path,
        topology_path=args.topology_path,
        compat_symlink_mode=args.compat_symlink_mode,
    )
    print(
        f"FLS DB built: {stats['paragraph_count']} paragraphs from "
        f"{stats['chapter_count']} chapters"
    )
    print(f"Commit: {stats['commit_sha']}")


if __name__ == "__main__":
    main()
