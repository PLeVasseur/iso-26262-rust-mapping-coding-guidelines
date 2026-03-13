"""Build `fls_spec` SQLite DB from parsed FLS RST paragraph sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from context.fls_topology import DEFAULT_TOPOLOGY_CACHE_PATH, load_topology_index
from retrieval.build.chunk_fts_validation import enforce_chunk_fts_mapping, refresh_chunk_fts_rowids
from retrieval.build.incremental_refresh import (
    audit_preserved_chunk_embeddings,
    capture_cross_db_baseline,
    build_fls_inventory,
    embedding_reuse_key,
    estimate_embedding_impact,
    ensure_incremental_tables,
    load_source_inventory_documents,
    plan_inventory_delta,
    prepare_staged_db,
    promote_staged_db,
    record_embedding_reuse_audit,
    record_materialization_delta,
    require_force_rebuild,
    replace_source_inventory,
    validate_cross_db_non_regression,
    validate_staged_corpus,
    write_refresh_contract_report,
    write_operator_summary,
    write_promotion_provenance,
    write_delta_report,
)
from retrieval.build.reports import (
    validate_chunk_first_db,
    write_chunk_first_validation_report,
    write_current_chunk_first_validation_report,
)
from retrieval.services.ws7_prework_closure import maybe_refresh_ws7_prework_closure_packet
from validate_ws7_mapping_audit import (
    DEFAULT_CLEANUP_OUTPUT_PATH,
    DEFAULT_OUTPUT_PATH as DEFAULT_WS7_AUDIT_OUTPUT_PATH,
    DEFAULT_DIFF_OUTPUT_PATH as DEFAULT_WS7_AUDIT_DIFF_OUTPUT_PATH,
    generate_mapping_audit,
    persist_mapping_audit_to_db,
    write_mapping_audit_diff,
    write_mapping_cleanup_tasks,
)

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
REPORT_ROOT = Path(".cache/sqlite_kb/reports/fls_spec")
COMPAT_DB_PATH = Path("data/fls_spec.db")
FLS_SOURCE_URL = "https://rust-lang.github.io/fls/index.html"
DEFAULT_EMBED_MODEL_ID = "Qwen/Qwen3-Embedding-4B"
DEFAULT_RERANKER_MODEL_ID = "BAAI/bge-reranker-v2-m3"
DEFAULT_EMBED_MODEL_REVISION = "unknown"
DEFAULT_RERANKER_MODEL_REVISION = "unknown"
DEFAULT_EMBED_MODEL_LICENSE = "unknown"
DEFAULT_RERANKER_MODEL_LICENSE = "unknown"
DEFAULT_EMBEDDING_DIM = 2560


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


def _load_source_metadata(source_dir: Path) -> dict[str, Any]:
    metadata_path = source_dir / "_metadata.json"
    if not metadata_path.exists():
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _token_len(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9_]+", text))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def _delete_fls_document_subtrees(
    connection: sqlite3.Connection, *, document_links: list[str]
) -> None:
    if not document_links:
        return
    placeholders = ", ".join("?" for _ in document_links)
    paragraph_rows = connection.execute(
        f"SELECT paragraph_id FROM paragraphs WHERE document_link IN ({placeholders})",
        tuple(document_links),
    ).fetchall()
    paragraph_ids = [str(row[0]) for row in paragraph_rows]
    section_rows = connection.execute(
        f"SELECT section_id FROM sections WHERE document_id IN ({placeholders})",
        tuple(document_links),
    ).fetchall()
    section_ids = [str(row[0]) for row in section_rows]
    if paragraph_ids:
        para_placeholders = ", ".join("?" for _ in paragraph_ids)
        for table_name in (
            "fls_paragraph_audit",
            "fls_paragraph_defined_terms",
            "fls_paragraph_term_refs",
            "fls_paragraph_syntax_defs",
            "fls_paragraph_syntax_refs",
            "fls_paragraph_std_refs",
            "fls_paragraph_refs",
        ):
            connection.execute(
                f"DELETE FROM {table_name} WHERE paragraph_id IN ({para_placeholders})",
                tuple(paragraph_ids),
            )
        connection.execute(
            f"DELETE FROM chunk_embeddings WHERE chunk_uid IN ({para_placeholders})",
            tuple(paragraph_ids),
        )
        connection.execute(
            f"DELETE FROM chunk_spans WHERE chunk_uid IN ({para_placeholders})",
            tuple(paragraph_ids),
        )
        connection.execute(
            f"DELETE FROM chunk_fts_rowids WHERE chunk_uid IN ({para_placeholders})",
            tuple(paragraph_ids),
        )
        connection.execute(
            f"DELETE FROM chunks_fts WHERE chunk_uid IN ({para_placeholders})",
            tuple(paragraph_ids),
        )
        connection.execute(
            f"DELETE FROM chunks WHERE chunk_uid IN ({para_placeholders})",
            tuple(paragraph_ids),
        )
        connection.execute(
            f"DELETE FROM paragraphs WHERE paragraph_id IN ({para_placeholders})",
            tuple(paragraph_ids),
        )
    if section_ids:
        section_placeholders = ", ".join("?" for _ in section_ids)
        connection.execute(
            f"DELETE FROM sections WHERE section_id IN ({section_placeholders})",
            tuple(section_ids),
        )
    connection.execute(
        f"DELETE FROM fls_sections WHERE document_link IN ({placeholders})",
        tuple(document_links),
    )
    connection.execute(
        f"DELETE FROM source_documents WHERE document_id IN ({placeholders})",
        tuple(document_links),
    )
    connection.execute(
        f"DELETE FROM docs WHERE doc_uid IN ({placeholders})",
        tuple(document_links),
    )
    connection.execute(
        f"DELETE FROM fls_documents WHERE document_link IN ({placeholders})",
        tuple(document_links),
    )


def _upsert_fls_document_rows(
    connection: sqlite3.Connection,
    *,
    snapshot_id: int,
    commit_sha: str,
    source_fetched_at: str,
    topology_index: dict[str, Any],
    document_links: set[str],
) -> None:
    for document in topology_index["documents_by_link"].values():
        if document_links and document.document_link not in document_links:
            continue
        document_sha = _sha256_text(
            json.dumps(
                {
                    "title": document.title,
                    "document_link": document.document_link,
                    "ordinal": document.ordinal,
                    "informational": bool(document.informational),
                },
                sort_keys=True,
            )
        )
        connection.execute(
            "INSERT OR REPLACE INTO fls_documents(document_link, title, ordinal, informational) VALUES(?, ?, ?, ?)",
            (
                document.document_link,
                document.title,
                document.ordinal,
                int(document.informational),
            ),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO source_documents(
                document_id, snapshot_id, chapter_id, rel_path, title,
                source_sha256, source_fetched_at, source_commit_sha, order_index
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.document_link,
                snapshot_id,
                document.document_link,
                document.document_link,
                document.title,
                document_sha,
                source_fetched_at,
                commit_sha,
                document.ordinal,
            ),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO docs(
                doc_uid, source_path, title, revision, fetched_at,
                source_sha256, chapter_id, order_index
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.document_link,
                document.document_link,
                document.title,
                commit_sha,
                source_fetched_at,
                document_sha,
                document.document_link,
                document.ordinal,
            ),
        )

    for section in topology_index["sections_by_link"].values():
        if document_links and section.document_link not in document_links:
            continue
        section_sha = _sha256_text(
            json.dumps(
                {
                    "section_id": section.section_id,
                    "section_link": section.section_link,
                    "document_link": section.document_link,
                    "title": section.title,
                    "number": section.number,
                    "ordinal": section.ordinal,
                    "informational": bool(section.informational),
                },
                sort_keys=True,
            )
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO fls_sections(
                section_link, section_id, document_link, title, number, ordinal, informational
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
        connection.execute(
            """
            INSERT OR REPLACE INTO sections(
                section_id, snapshot_id, document_id, chapter_id, anchor,
                heading, order_index, level, text, source_sha256,
                source_fetched_at, source_commit_sha
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                section.section_id,
                snapshot_id,
                section.document_link,
                section.document_link,
                section.section_link,
                section.title,
                section.ordinal,
                1,
                section.title,
                section_sha,
                source_fetched_at,
                commit_sha,
            ),
        )


def _upsert_fls_paragraph_row(
    connection: sqlite3.Connection,
    *,
    snapshot_id: int,
    commit_sha: str,
    source_fetched_at: str,
    paragraph: Any,
    live_paragraph_ids: dict[str, Any],
    retrieval_order_index: int,
) -> bool:
    live_topology_present = paragraph.paragraph_id in live_paragraph_ids
    retrieval_status = "canonical-and-retrieval-eligible"
    audit_note = ""
    if not live_topology_present:
        retrieval_status = "canonical-but-non-retrieval-eligible-due-to-live-topology-absence"
        audit_note = "parsed paragraph_id absent from live topology"
    elif not str(paragraph.section_id).strip():
        retrieval_status = "canonical-but-non-retrieval-eligible-due-to-missing-section-id"
        audit_note = "live-topology paragraph metadata did not provide canonical section_id"

    retrieval_eligible = bool(live_topology_present and bool(str(paragraph.section_id).strip()))
    connection.execute(
        """
        INSERT OR REPLACE INTO paragraphs(
            paragraph_id, paragraph_number, chapter, section, subsection, text,
            raw_text, clean_text, source_file, document_link, paragraph_link,
            section_link, section_id, checksum, live_topology_present,
            retrieval_eligible, retrieval_status, snapshot_id
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            paragraph.paragraph_id,
            paragraph.paragraph_number,
            paragraph.chapter,
            paragraph.section,
            paragraph.subsection,
            paragraph.clean_text,
            paragraph.raw_text,
            paragraph.clean_text,
            paragraph.source_file,
            paragraph.document_link,
            paragraph.paragraph_link,
            paragraph.section_link,
            paragraph.section_id,
            paragraph.checksum,
            int(live_topology_present),
            int(retrieval_eligible),
            retrieval_status,
            snapshot_id,
        ),
    )
    connection.execute(
        "INSERT OR REPLACE INTO fls_paragraph_audit(paragraph_id, live_topology_present, retrieval_eligible, retrieval_status, note) VALUES(?, ?, ?, ?, ?)",
        (
            paragraph.paragraph_id,
            int(live_topology_present),
            int(retrieval_eligible),
            retrieval_status,
            audit_note,
        ),
    )
    if retrieval_eligible:
        chunk_source_sha = paragraph.checksum or _sha256_text(paragraph.raw_text)
        connection.execute(
            "INSERT OR REPLACE INTO chunks(chunk_uid, section_id, raw_text, clean_text, char_len, token_len, source_sha256, source_fetched_at, source_commit_sha, order_index) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                paragraph.paragraph_id,
                paragraph.section_id,
                paragraph.raw_text,
                paragraph.clean_text,
                len(paragraph.clean_text),
                _token_len(paragraph.clean_text),
                chunk_source_sha,
                source_fetched_at,
                commit_sha,
                retrieval_order_index,
            ),
        )
        connection.execute(
            "INSERT OR REPLACE INTO chunk_spans(chunk_uid, source_anchor, start_offset, end_offset, span_order) VALUES(?, ?, ?, ?, ?)",
            (
                paragraph.paragraph_id,
                paragraph.paragraph_link,
                0,
                len(paragraph.clean_text),
                1,
            ),
        )
        connection.execute(
            "DELETE FROM chunks_fts WHERE chunk_uid = ?",
            (paragraph.paragraph_id,),
        )
        connection.execute(
            "INSERT OR REPLACE INTO chunks_fts(chunk_uid, section_id, section_heading, chunk_text) VALUES(?, ?, ?, ?)",
            (
                paragraph.paragraph_id,
                paragraph.section_id,
                paragraph.section or paragraph.chapter,
                paragraph.clean_text,
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
    for ordinal, (_value, target) in enumerate(
        zip(paragraph.defined_terms, paragraph.defined_term_targets, strict=True)
    ):
        connection.execute(
            "UPDATE fls_paragraph_defined_terms SET term_target = ? WHERE paragraph_id = ? AND term_order = ?",
            (target, paragraph.paragraph_id, ordinal),
        )
    _insert_ordered_text_rows(
        connection,
        table_name="fls_paragraph_term_refs",
        value_column="term_text",
        order_column="term_order",
        paragraph_id=paragraph.paragraph_id,
        values=paragraph.term_refs,
    )
    for ordinal, (_value, target) in enumerate(
        zip(paragraph.term_refs, paragraph.term_ref_targets, strict=True)
    ):
        connection.execute(
            "UPDATE fls_paragraph_term_refs SET term_target = ? WHERE paragraph_id = ? AND term_order = ?",
            (target, paragraph.paragraph_id, ordinal),
        )
    _insert_ordered_text_rows(
        connection,
        table_name="fls_paragraph_syntax_defs",
        value_column="symbol_text",
        order_column="symbol_order",
        paragraph_id=paragraph.paragraph_id,
        values=paragraph.syntax_defs,
    )
    for ordinal, (_value, target) in enumerate(
        zip(paragraph.syntax_defs, paragraph.syntax_def_targets, strict=True)
    ):
        connection.execute(
            "UPDATE fls_paragraph_syntax_defs SET symbol_target = ? WHERE paragraph_id = ? AND symbol_order = ?",
            (target, paragraph.paragraph_id, ordinal),
        )
    _insert_ordered_text_rows(
        connection,
        table_name="fls_paragraph_syntax_refs",
        value_column="symbol_text",
        order_column="symbol_order",
        paragraph_id=paragraph.paragraph_id,
        values=paragraph.syntax_refs,
    )
    for ordinal, (_value, target) in enumerate(
        zip(paragraph.syntax_refs, paragraph.syntax_ref_targets, strict=True)
    ):
        connection.execute(
            "UPDATE fls_paragraph_syntax_refs SET symbol_target = ? WHERE paragraph_id = ? AND symbol_order = ?",
            (target, paragraph.paragraph_id, ordinal),
        )
    _insert_ordered_text_rows(
        connection,
        table_name="fls_paragraph_std_refs",
        value_column="symbol_text",
        order_column="symbol_order",
        paragraph_id=paragraph.paragraph_id,
        values=paragraph.std_refs,
    )
    for ordinal, (_value, target) in enumerate(
        zip(paragraph.std_refs, paragraph.std_ref_targets, strict=True)
    ):
        connection.execute(
            "UPDATE fls_paragraph_std_refs SET symbol_target = ? WHERE paragraph_id = ? AND symbol_order = ?",
            (target, paragraph.paragraph_id, ordinal),
        )
    for ordinal, (ref_text, ref_target) in enumerate(
        zip(paragraph.paragraph_refs, paragraph.paragraph_ref_targets, strict=True)
    ):
        connection.execute(
            "INSERT INTO fls_paragraph_refs(paragraph_id, ref_text, ref_target, ref_order) VALUES(?, ?, ?, ?)",
            (paragraph.paragraph_id, ref_text, ref_target, ordinal),
        )
    return retrieval_eligible


def build_fls_db(
    source_dir: Path = FLS_SOURCE_DIR,
    db_path: Path = DB_PATH,
    spec_lock_path: Path = DEFAULT_SPEC_LOCK_PATH,
    topology_path: Path = DEFAULT_TOPOLOGY_PATH,
    compat_symlink_mode: Literal["auto", "always", "never"] = "auto",
    report_root: Path = REPORT_ROOT,
    ws7_audit_output_path: Path | None = None,
    ws7_cleanup_output_path: Path | None = None,
    ws7_diff_output_path: Path | None = None,
    incremental: bool = False,
    force_rebuild: bool = False,
    staged_output_root: Path | None = None,
    promotion_root: Path | None = None,
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
    source_metadata = _load_source_metadata(source_dir)
    source_fetched_at = str(source_metadata.get("fetched_at") or _utc_now())
    source_state_sha = _sha256_text(json.dumps(source_metadata, sort_keys=True))
    chapters = sorted({paragraph.chapter for paragraph in paragraphs if paragraph.chapter})
    document_inventory, section_inventory, unit_inventory = build_fls_inventory(
        paragraphs=paragraphs
    )
    staged_root = staged_output_root or (db_path.parents[1] / "staged")
    promotion_root = promotion_root or (db_path.parents[1] / "promotions")
    run_id = f"fls_spec_incremental::{commit_sha}"
    target_db_path = db_path
    use_incremental = bool(incremental)
    cross_db_baseline = capture_cross_db_baseline(root=PROJECT_ROOT) if use_incremental else {}
    promotion: dict[str, str] = {}
    planned_delta = plan_inventory_delta(current={}, incoming=document_inventory)
    if use_incremental:
        target_db_path, _ = prepare_staged_db(
            live_db_path=db_path,
            staged_root=staged_root,
            corpus="fls_spec",
            run_id=run_id,
        )
        if target_db_path.exists():
            prior_connection = sqlite3.connect(str(target_db_path))
            try:
                planned_delta = plan_inventory_delta(
                    current=load_source_inventory_documents(prior_connection, corpus="fls_spec"),
                    incoming=document_inventory,
                )
            finally:
                prior_connection.close()
    unchanged_documents = set(planned_delta.unchanged)
    unchanged_sections = {
        section_id
        for section_id, entry in section_inventory.items()
        if entry.parent_id in unchanged_documents
    }
    unchanged_chunk_ids = [
        unit_id
        for (unit_kind, unit_id), entry in unit_inventory.items()
        if unit_kind == "chunk" and entry.parent_id in unchanged_sections
    ]
    dry_run_report_path = write_delta_report(
        report_root=report_root,
        corpus="fls_spec",
        run_id=run_id,
        phase="pre_apply",
        payload={
            **planned_delta.as_dict(),
            "snapshot_id": f"fls-spec-{commit_sha}",
            "db_path": str(target_db_path),
            "staged": use_incremental,
            "embedding_impact": estimate_embedding_impact(
                planned_delta=planned_delta,
                unchanged_chunk_ids=unchanged_chunk_ids,
                changed_chunk_ids=[
                    unit_id
                    for (unit_kind, unit_id), entry in unit_inventory.items()
                    if unit_kind == "chunk" and entry.parent_id not in unchanged_sections
                ],
            ),
        },
    )

    target_db_path.parent.mkdir(parents=True, exist_ok=True)
    incremental_apply = bool(
        use_incremental and target_db_path.exists() and target_db_path.stat().st_size
    )
    if not incremental_apply and target_db_path.exists():
        target_db_path.unlink()

    chunk_mapping: dict[str, Any] = {"applicable": False, "refreshed_rows": 0}
    chunk_mapping_diagnostics: dict[str, Any] = {
        "applicable": False,
        "scope": "not_chunk_first",
        "passed": True,
    }
    connection = sqlite3.connect(str(target_db_path))
    try:
        if not incremental_apply:
            connection.executescript(
                """
            CREATE TABLE snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                commit_sha TEXT NOT NULL,
                source_url TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL DEFAULT '',
                sha256 TEXT NOT NULL DEFAULT '',
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
                raw_text TEXT NOT NULL,
                clean_text TEXT NOT NULL,
                source_file TEXT NOT NULL,
                document_link TEXT NOT NULL,
                paragraph_link TEXT NOT NULL,
                section_link TEXT NOT NULL,
                section_id TEXT NOT NULL DEFAULT '',
                checksum TEXT NOT NULL DEFAULT '',
                live_topology_present INTEGER NOT NULL DEFAULT 0,
                retrieval_eligible INTEGER NOT NULL DEFAULT 0,
                retrieval_status TEXT NOT NULL DEFAULT '',
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id)
            );

            CREATE TABLE fls_paragraph_audit (
                paragraph_id TEXT PRIMARY KEY,
                live_topology_present INTEGER NOT NULL,
                retrieval_eligible INTEGER NOT NULL,
                retrieval_status TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT ''
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
                term_target TEXT NOT NULL DEFAULT '',
                term_order INTEGER NOT NULL
            );

            CREATE TABLE fls_paragraph_term_refs (
                paragraph_id TEXT NOT NULL,
                term_text TEXT NOT NULL,
                term_target TEXT NOT NULL DEFAULT '',
                term_order INTEGER NOT NULL
            );

            CREATE TABLE fls_paragraph_syntax_defs (
                paragraph_id TEXT NOT NULL,
                symbol_text TEXT NOT NULL,
                symbol_target TEXT NOT NULL DEFAULT '',
                symbol_order INTEGER NOT NULL
            );

            CREATE TABLE fls_paragraph_syntax_refs (
                paragraph_id TEXT NOT NULL,
                symbol_text TEXT NOT NULL,
                symbol_target TEXT NOT NULL DEFAULT '',
                symbol_order INTEGER NOT NULL
            );

            CREATE TABLE fls_paragraph_std_refs (
                paragraph_id TEXT NOT NULL,
                symbol_text TEXT NOT NULL,
                symbol_target TEXT NOT NULL DEFAULT '',
                symbol_order INTEGER NOT NULL
            );

            CREATE TABLE fls_paragraph_refs (
                paragraph_id TEXT NOT NULL,
                ref_text TEXT NOT NULL DEFAULT '',
                ref_target TEXT NOT NULL,
                ref_order INTEGER NOT NULL
            );

            CREATE TABLE source_documents (
                document_id TEXT PRIMARY KEY,
                snapshot_id INTEGER NOT NULL,
                chapter_id TEXT NOT NULL DEFAULT '',
                rel_path TEXT NOT NULL,
                title TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                source_fetched_at TEXT NOT NULL,
                source_commit_sha TEXT NOT NULL,
                order_index INTEGER NOT NULL
            );

            CREATE TABLE docs (
                doc_uid TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                title TEXT NOT NULL,
                revision TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                chapter_id TEXT NOT NULL DEFAULT '',
                order_index INTEGER NOT NULL
            );

            CREATE TABLE sections (
                section_id TEXT PRIMARY KEY,
                snapshot_id INTEGER NOT NULL,
                document_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL DEFAULT '',
                anchor TEXT NOT NULL,
                heading TEXT NOT NULL,
                order_index INTEGER NOT NULL,
                level INTEGER NOT NULL,
                text TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                source_fetched_at TEXT NOT NULL,
                source_commit_sha TEXT NOT NULL
            );

            CREATE TABLE chunks (
                chunk_uid TEXT PRIMARY KEY,
                section_id TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                clean_text TEXT NOT NULL,
                char_len INTEGER NOT NULL,
                token_len INTEGER NOT NULL,
                source_sha256 TEXT NOT NULL,
                source_fetched_at TEXT NOT NULL,
                source_commit_sha TEXT NOT NULL,
                order_index INTEGER NOT NULL
            );

            CREATE TABLE chunk_spans (
                chunk_uid TEXT NOT NULL,
                source_anchor TEXT NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                span_order INTEGER NOT NULL,
                PRIMARY KEY (chunk_uid, span_order)
            );

            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                chunk_uid UNINDEXED,
                section_id UNINDEXED,
                section_heading,
                chunk_text,
                tokenize='unicode61 remove_diacritics 2'
            );

            CREATE TABLE chunk_fts_rowids (
                chunk_uid TEXT PRIMARY KEY,
                fts_rowid INTEGER NOT NULL UNIQUE
            );

            CREATE TABLE semantic_models (
                model_id TEXT PRIMARY KEY,
                model_role TEXT NOT NULL,
                model_name TEXT NOT NULL,
                model_revision TEXT NOT NULL,
                embedding_dim INTEGER NOT NULL,
                distance_metric TEXT NOT NULL,
                license TEXT NOT NULL,
                provider TEXT NOT NULL,
                retrieval_mode TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE chunk_embeddings (
                chunk_uid TEXT NOT NULL,
                model_id TEXT NOT NULL,
                embed_version TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                vector_json TEXT NOT NULL,
                vector_norm REAL NOT NULL,
                embedded_at TEXT NOT NULL,
                source_fetched_at TEXT NOT NULL,
                PRIMARY KEY(chunk_uid, model_id, embed_version)
            );

            CREATE VIRTUAL TABLE paragraphs_fts USING fts5(
                paragraph_id,
                paragraph_number,
                chapter,
                section,
                subsection,
                document_link,
                section_link,
                clean_text,
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
                    clean_text
                ) VALUES (
                    new.rowid,
                    new.paragraph_id,
                    new.paragraph_number,
                    new.chapter,
                    new.section,
                    new.subsection,
                    new.document_link,
                    new.section_link,
                    new.clean_text
                );
            END;

            CREATE INDEX idx_paragraphs_chapter ON paragraphs(chapter);
            CREATE INDEX idx_paragraphs_section ON paragraphs(section);
            CREATE INDEX idx_paragraphs_document_link ON paragraphs(document_link);
            CREATE INDEX idx_paragraphs_section_link ON paragraphs(section_link);
            CREATE INDEX idx_paragraphs_retrieval_eligible ON paragraphs(retrieval_eligible, paragraph_id);
            CREATE INDEX idx_defined_terms_paragraph_id ON fls_paragraph_defined_terms(paragraph_id);
            CREATE INDEX idx_term_refs_paragraph_id ON fls_paragraph_term_refs(paragraph_id);
            CREATE INDEX idx_syntax_defs_paragraph_id ON fls_paragraph_syntax_defs(paragraph_id);
            CREATE INDEX idx_syntax_refs_paragraph_id ON fls_paragraph_syntax_refs(paragraph_id);
            CREATE INDEX idx_std_refs_paragraph_id ON fls_paragraph_std_refs(paragraph_id);
            CREATE INDEX idx_paragraph_refs_paragraph_id ON fls_paragraph_refs(paragraph_id);
            CREATE INDEX idx_sections_document ON sections(document_id, order_index);
            CREATE INDEX idx_chunks_section ON chunks(section_id, order_index);
            CREATE INDEX idx_chunk_fts_rowids_fts_rowid ON chunk_fts_rowids(fts_rowid);
            CREATE INDEX idx_chunk_spans_anchor ON chunk_spans(source_anchor, chunk_uid);
            CREATE INDEX idx_chunk_embeddings_model ON chunk_embeddings(model_id, chunk_uid, embed_version);
            """
            )

        cursor = connection.execute(
            """
            INSERT INTO snapshots(
                commit_sha,
                source_url,
                fetched_at,
                sha256,
                paragraph_count,
                chapter_count,
                document_count,
                section_count
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                commit_sha,
                FLS_SOURCE_URL,
                source_fetched_at,
                source_state_sha,
                len(paragraphs),
                len(chapters),
                len(topology_index["documents_by_link"]),
                len(topology_index["sections_by_link"]),
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Failed to create FLS snapshot row")
        snapshot_id = int(cursor.lastrowid)

        changed_document_links = set(planned_delta.updated) | set(planned_delta.deleted)
        insert_document_links = set(planned_delta.added) | set(planned_delta.updated)
        if not incremental_apply:
            insert_document_links = set(document_inventory)
        else:
            _delete_fls_document_subtrees(
                connection,
                document_links=sorted(changed_document_links),
            )

        _upsert_fls_document_rows(
            connection,
            snapshot_id=snapshot_id,
            commit_sha=commit_sha,
            source_fetched_at=source_fetched_at,
            topology_index=topology_index,
            document_links=insert_document_links,
        )

        semantic_model_rows = [
            (
                DEFAULT_EMBED_MODEL_ID,
                "embedder",
                DEFAULT_EMBED_MODEL_ID,
                DEFAULT_EMBED_MODEL_REVISION,
                DEFAULT_EMBEDDING_DIM,
                "cosine",
                DEFAULT_EMBED_MODEL_LICENSE,
                "huggingface-tei",
                "hybrid",
                source_fetched_at,
            ),
            (
                DEFAULT_RERANKER_MODEL_ID,
                "reranker",
                DEFAULT_RERANKER_MODEL_ID,
                DEFAULT_RERANKER_MODEL_REVISION,
                0,
                "n/a",
                DEFAULT_RERANKER_MODEL_LICENSE,
                "huggingface-tei",
                "hybrid",
                source_fetched_at,
            ),
        ]
        if incremental_apply:
            connection.execute("DELETE FROM semantic_models")
        connection.executemany(
            """
            INSERT INTO semantic_models(
                model_id,
                model_role,
                model_name,
                model_revision,
                embedding_dim,
                distance_metric,
                license,
                provider,
                retrieval_mode,
                created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            semantic_model_rows,
        )

        if incremental_apply:
            retrieval_order_index_row = connection.execute(
                "SELECT COALESCE(MAX(order_index), 0) FROM chunks"
            ).fetchone()
            retrieval_order_index = int(retrieval_order_index_row[0] or 0)
        else:
            retrieval_order_index = 0
        live_paragraph_ids = topology_index["paragraphs_by_id"]
        for paragraph in paragraphs:
            if incremental_apply and paragraph.document_link not in insert_document_links:
                continue
            retrieval_order_index += 1
            retrieval_eligible = _upsert_fls_paragraph_row(
                connection,
                snapshot_id=snapshot_id,
                commit_sha=commit_sha,
                source_fetched_at=source_fetched_at,
                paragraph=paragraph,
                live_paragraph_ids=live_paragraph_ids,
                retrieval_order_index=retrieval_order_index,
            )
            if not retrieval_eligible:
                retrieval_order_index -= 1

        retrieval_eligible_count_row = connection.execute(
            "SELECT COUNT(*) FROM paragraphs WHERE retrieval_eligible = 1"
        ).fetchone()
        paragraph_count_row = connection.execute("SELECT COUNT(*) FROM paragraphs").fetchone()
        retrieval_eligible_count = int(retrieval_eligible_count_row[0] or 0)
        audit_only_count = int(paragraph_count_row[0] or 0) - retrieval_eligible_count

        chunk_mapping = refresh_chunk_fts_rowids(connection)
        chunk_mapping_diagnostics = enforce_chunk_fts_mapping(
            connection,
            context="fls_spec build",
        )

        connection.commit()
    finally:
        connection.close()

    report_root.mkdir(parents=True, exist_ok=True)
    snapshot_id = f"fls-spec-{commit_sha}"
    inventory_connection = sqlite3.connect(str(target_db_path))
    try:
        ensure_incremental_tables(inventory_connection)
        replace_source_inventory(
            inventory_connection,
            corpus="fls_spec",
            snapshot_id=snapshot_id,
            documents=document_inventory,
            sections=section_inventory,
            units=unit_inventory,
            materialized_at=_utc_now(),
        )
        record_materialization_delta(
            inventory_connection,
            run_id=run_id,
            corpus="fls_spec",
            mode="incremental" if use_incremental else "full_rebuild",
            base_snapshot_id="",
            target_snapshot_id=snapshot_id,
            delta_payload={
                **planned_delta.as_dict(),
                "snapshot_id": snapshot_id,
                "force_rebuild": force_rebuild,
            },
        )
        embedding_audit = audit_preserved_chunk_embeddings(
            inventory_connection,
            corpus="fls_spec",
            unchanged_chunk_ids=unchanged_chunk_ids if use_incremental else [],
        )
        record_embedding_reuse_audit(
            inventory_connection,
            run_id=run_id,
            corpus="fls_spec",
            model_fingerprint=embedding_reuse_key(
                stable_id="fls_spec",
                content_sha256=source_state_sha,
                model_id=DEFAULT_EMBED_MODEL_ID,
                embed_version="v1",
            ),
            reused_count=int(embedding_audit["reused_count"]),
            recomputed_count=int(embedding_audit["recomputed_count"]),
        )
        inventory_connection.commit()
    finally:
        inventory_connection.close()

    delta_report_path = write_delta_report(
        report_root=report_root,
        corpus="fls_spec",
        run_id=run_id,
        payload={
            **planned_delta.as_dict(),
            "snapshot_id": snapshot_id,
            "db_path": str(target_db_path),
            "staged": use_incremental,
        },
    )
    dry_run_report_path = write_delta_report(
        report_root=report_root,
        corpus="fls_spec",
        run_id=run_id,
        phase="pre_apply",
        payload={
            **planned_delta.as_dict(),
            "snapshot_id": snapshot_id,
            "db_path": str(target_db_path),
            "staged": use_incremental,
            "embedding_impact": estimate_embedding_impact(
                planned_delta=planned_delta,
                unchanged_chunk_ids=unchanged_chunk_ids,
                changed_chunk_ids=[
                    unit_id
                    for (unit_kind, unit_id), entry in unit_inventory.items()
                    if unit_kind == "chunk" and entry.parent_id not in unchanged_sections
                ],
            ),
        },
    )
    refresh_contract_path = write_refresh_contract_report(
        report_root=report_root,
        corpus="fls_spec",
        run_id=run_id,
    )
    audit_output_path = ws7_audit_output_path or DEFAULT_WS7_AUDIT_OUTPUT_PATH
    cleanup_output_path = ws7_cleanup_output_path or DEFAULT_CLEANUP_OUTPUT_PATH
    diff_output_path = ws7_diff_output_path or DEFAULT_WS7_AUDIT_DIFF_OUTPUT_PATH
    previous_audit_payload = {}
    if audit_output_path.exists():
        previous_audit_payload = json.loads(audit_output_path.read_text(encoding="utf-8"))
    audit_payload = generate_mapping_audit(
        fls_db_path=target_db_path,
        guidelines_root=(PROJECT_ROOT / ".." / "safety-critical-rust-coding-guidelines").resolve(),
        guidelines_db_path=PROJECT_ROOT
        / ".cache"
        / "sqlite_kb"
        / "current"
        / "guidelines_repo.sqlite",
        output_path=audit_output_path,
        heldout_manifest_path=PROJECT_ROOT / "data" / "fls_ws7_heldout_manifest.json",
        publishability_audit_path=(
            PROJECT_ROOT
            / ".cache"
            / "sqlite_kb"
            / "reports"
            / "writer_publish"
            / "v17_2_closure_23_reviewer_hardened_ws7"
            / "publishability_audit.json"
        ),
    )
    cleanup_payload = write_mapping_cleanup_tasks(
        audit_payload=audit_payload,
        output_path=cleanup_output_path,
    )
    diff_payload = write_mapping_audit_diff(
        previous_payload=previous_audit_payload,
        current_payload=audit_payload,
        output_path=diff_output_path,
    )
    persist_mapping_audit_to_db(
        fls_db_path=target_db_path,
        audit_payload=audit_payload,
        cleanup_payload=cleanup_payload,
    )

    chunk_first_report = validate_chunk_first_db(target_db_path, corpus="fls_spec")
    chunk_first_report_path = write_chunk_first_validation_report(
        report_root=report_root,
        corpus="fls_spec",
        snapshot_id=snapshot_id,
        payload=chunk_first_report,
    )
    write_current_chunk_first_validation_report(
        report_root=report_root,
        corpus="fls_spec",
        payload=chunk_first_report,
    )
    maybe_refresh_ws7_prework_closure_packet(
        root=Path(__file__).resolve().parents[1],
        deferred_items=["WS7 staged runtime implementation"],
    )
    if not chunk_first_report["passed"]:
        raise RuntimeError(
            f"Chunk-first validation failed for fls_spec.db: {chunk_first_report['failures']}"
        )
    if use_incremental:
        try:
            stage_reports = validate_staged_corpus(corpus="fls_spec", staged_db_path=target_db_path)
            cross_db_report_path = validate_cross_db_non_regression(
                root=PROJECT_ROOT,
                report_root=report_root,
                run_id=run_id,
                target_corpus="fls_spec",
                baseline=cross_db_baseline,
                additional_stage_reports=stage_reports,
                target_staged_db_path=target_db_path,
            )
            promotion = promote_staged_db(
                live_db_path=db_path,
                staged_db_path=target_db_path,
                promotion_root=promotion_root,
                corpus="fls_spec",
                run_id=run_id,
            )
            audit_payload = generate_mapping_audit(
                fls_db_path=db_path,
                guidelines_root=(
                    PROJECT_ROOT / ".." / "safety-critical-rust-coding-guidelines"
                ).resolve(),
                guidelines_db_path=PROJECT_ROOT
                / ".cache"
                / "sqlite_kb"
                / "current"
                / "guidelines_repo.sqlite",
                output_path=audit_output_path,
                heldout_manifest_path=PROJECT_ROOT / "data" / "fls_ws7_heldout_manifest.json",
                publishability_audit_path=(
                    PROJECT_ROOT
                    / ".cache"
                    / "sqlite_kb"
                    / "reports"
                    / "writer_publish"
                    / "v17_2_closure_23_reviewer_hardened_ws7"
                    / "publishability_audit.json"
                ),
            )
            cleanup_payload = write_mapping_cleanup_tasks(
                audit_payload=audit_payload,
                output_path=cleanup_output_path,
            )
            diff_payload = write_mapping_audit_diff(
                previous_payload=previous_audit_payload,
                current_payload=audit_payload,
                output_path=diff_output_path,
            )
            persist_mapping_audit_to_db(
                fls_db_path=db_path,
                audit_payload=audit_payload,
                cleanup_payload=cleanup_payload,
            )
            promotion_provenance_path = write_promotion_provenance(
                report_root=report_root,
                corpus="fls_spec",
                run_id=run_id,
                payload={
                    "run_id": run_id,
                    "corpus": "fls_spec",
                    "validated_at": _utc_now(),
                    "validation_reports": stage_reports,
                    "ws7_mapping_audit_path": str(audit_output_path),
                    "ws7_mapping_cleanup_tasks_path": str(cleanup_output_path),
                    "ws7_mapping_audit_diff_path": str(diff_output_path),
                    "refresh_contract_path": str(refresh_contract_path),
                    "cross_db_report_path": str(cross_db_report_path),
                    "promotion": promotion,
                },
            )
            promotion["promotion_provenance_path"] = str(promotion_provenance_path)
            operator_summary_path = write_operator_summary(
                report_root=report_root,
                corpus="fls_spec",
                run_id=run_id,
                payload={
                    "corpus": "fls_spec",
                    "run_id": run_id,
                    "status": "promoted",
                    "dry_run_report_path": str(dry_run_report_path),
                    "delta_report_path": str(delta_report_path),
                    "refresh_contract_path": str(refresh_contract_path),
                    "ws7_mapping_audit_path": str(audit_output_path),
                    "ws7_mapping_cleanup_tasks_path": str(cleanup_output_path),
                    "ws7_mapping_audit_diff_path": str(diff_output_path),
                    "cross_db_report_path": str(cross_db_report_path),
                    "promotion_provenance_path": str(promotion_provenance_path),
                    "validation_kinds": [item["kind"] for item in stage_reports],
                },
            )
            promotion["operator_summary_path"] = str(operator_summary_path)
        except Exception as exc:
            require_force_rebuild(
                corpus="fls_spec",
                reason=str(exc),
                force_rebuild=force_rebuild,
            )
            return build_fls_db(
                source_dir=source_dir,
                db_path=db_path,
                spec_lock_path=spec_lock_path,
                topology_path=topology_path,
                compat_symlink_mode=compat_symlink_mode,
                report_root=report_root,
                incremental=False,
                force_rebuild=False,
                staged_output_root=staged_output_root,
                promotion_root=promotion_root,
            )
    if _should_update_compat_symlink(db_path=db_path, compat_symlink_mode=compat_symlink_mode):
        _ensure_compat_symlink(db_path)

    return {
        "db_path": str(db_path),
        "commit_sha": commit_sha,
        "paragraph_count": len(paragraphs),
        "retrieval_eligible_count": retrieval_eligible_count,
        "audit_only_count": audit_only_count,
        "chapter_count": len(chapters),
        "document_count": len(topology_index["documents_by_link"]),
        "section_count": len(topology_index["sections_by_link"]),
        "chapters": chapters,
        "chunk_first_report_path": str(chunk_first_report_path),
        "delta_report_path": str(delta_report_path),
        "dry_run_report_path": str(dry_run_report_path),
        "refresh_contract_path": str(refresh_contract_path),
        "ws7_mapping_audit_path": str(audit_output_path),
        "ws7_mapping_cleanup_tasks_path": str(cleanup_output_path),
        "ws7_mapping_audit_diff_path": str(diff_output_path),
        "ws7_mapping_classification_counts": audit_payload.get("classification_counts", {}),
        "ws7_mapping_cleanup_task_count": cleanup_payload.get("task_count", 0),
        "ws7_mapping_changed_rows": len(list(diff_payload.get("changed_rows") or [])),
        "incremental": use_incremental,
        "promotion": promotion,
        "chunk_fts_mapping": {
            **chunk_mapping,
            **chunk_mapping_diagnostics,
        },
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
    parser.set_defaults(incremental=True)
    parser.add_argument("--incremental", dest="incremental", action="store_true")
    parser.add_argument("--no-incremental", dest="incremental", action="store_false")
    parser.add_argument("--force-rebuild", action="store_true")
    parser.add_argument(
        "--staged-output-root",
        type=Path,
        default=Path(".cache/sqlite_kb/staged"),
    )
    parser.add_argument(
        "--promotion-root",
        type=Path,
        default=Path(".cache/sqlite_kb/promotions"),
    )
    args = parser.parse_args()

    stats = build_fls_db(
        source_dir=args.source_dir,
        db_path=args.db_path,
        spec_lock_path=args.spec_lock_path,
        topology_path=args.topology_path,
        compat_symlink_mode=args.compat_symlink_mode,
        incremental=bool(args.incremental),
        force_rebuild=bool(args.force_rebuild),
        staged_output_root=args.staged_output_root,
        promotion_root=args.promotion_root,
    )
    print(
        f"FLS DB built: {stats['paragraph_count']} paragraphs "
        f"({stats['retrieval_eligible_count']} retrieval-eligible, {stats['audit_only_count']} audit-only) "
        f"from {stats['chapter_count']} chapters"
    )
    print(f"Commit: {stats['commit_sha']}")


if __name__ == "__main__":
    main()
