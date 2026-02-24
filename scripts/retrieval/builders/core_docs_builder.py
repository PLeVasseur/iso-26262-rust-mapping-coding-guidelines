from __future__ import annotations

import hashlib
import sqlite3
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path

from retrieval.core.provenance import (
    apply_pending_migrations,
    canonical_json_hash,
    compute_source_state_from_db,
    record_pipeline_run,
)
from retrieval.operations.build import (
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_MODEL_ID,
    DEFAULT_RERANKER_MODEL_ID,
    DEFAULT_TABLE_NODE_ID,
    _resolve_table1_rows,
    initialize_schema,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _token_count(text: str) -> int:
    return max(1, len([token for token in text.split() if token]))


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return int(default)


def _insert_core_docs_payload(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    commit_sha: str,
    fetched_at: str,
    table_rows: list[dict[str, object]],
) -> None:
    snapshot_sha = _sha256_text("::".join((snapshot_id, commit_sha, fetched_at)))
    connection.execute(
        """
        INSERT INTO snapshots(snapshot_id, commit_sha, source_url, fetched_at, sha256)
        VALUES(?, ?, ?, ?, ?)
        """,
        (snapshot_id, commit_sha, "https://doc.rust-lang.org/core/", fetched_at, snapshot_sha),
    )

    for row in table_rows:
        row_node_id = str(row["row_node_id"])
        row_marker = str(row["row_marker"])
        row_idx = _as_int(row["row_idx"])
        requirement_text = str(row["requirement_text"])
        raw_terms = row.get("row_profile_terms")
        row_terms: list[str] = []
        if isinstance(raw_terms, list):
            row_terms = [str(term).strip().lower() for term in raw_terms]
        if not row_terms:
            row_terms = [token for token in requirement_text.lower().split() if len(token) >= 4][:8]

        chapter_id = "chapter:001:core-docs"
        document_id = f"core-docs::{row_marker}"
        section_id = f"core-docs::{row_marker}::section"
        source_sha = _sha256_text(requirement_text)
        anchor = f"row-{row_marker}"
        chunk_text = (
            f"Core docs coverage for ISO 26262 Table 1 row {row_marker}. "
            f"Requirement: {requirement_text} "
            f"Profile terms: {', '.join(row_terms)}"
        )
        chunk_uid = f"chunk::{_sha256_text(section_id + '::' + chunk_text.lower())}"

        connection.execute(
            """
            INSERT INTO source_documents(
                document_id,
                snapshot_id,
                chapter_id,
                rel_path,
                title,
                source_sha256,
                source_fetched_at,
                source_commit_sha,
                order_index
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                snapshot_id,
                chapter_id,
                f"core/{row_marker}.md",
                f"Core docs row {row_marker}",
                source_sha,
                fetched_at,
                commit_sha,
                row_idx,
            ),
        )

        connection.execute(
            """
            INSERT INTO docs(
                doc_uid,
                source_path,
                title,
                revision,
                fetched_at,
                source_sha256,
                chapter_id,
                order_index
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                f"core/{row_marker}.md",
                f"Core docs row {row_marker}",
                commit_sha,
                fetched_at,
                source_sha,
                chapter_id,
                row_idx,
            ),
        )

        connection.execute(
            """
            INSERT INTO sections(
                section_id,
                snapshot_id,
                document_id,
                chapter_id,
                anchor,
                heading,
                order_index,
                level,
                text,
                source_sha256,
                source_fetched_at,
                source_commit_sha
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                section_id,
                snapshot_id,
                document_id,
                chapter_id,
                anchor,
                f"ISO Row {row_marker}",
                row_idx,
                2,
                chunk_text,
                source_sha,
                fetched_at,
                commit_sha,
            ),
        )

        connection.execute(
            """
            INSERT INTO chunks(
                chunk_uid,
                section_id,
                raw_text,
                clean_text,
                char_len,
                token_len,
                source_sha256,
                source_fetched_at,
                source_commit_sha,
                order_index
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk_uid,
                section_id,
                chunk_text,
                chunk_text,
                len(chunk_text),
                _token_count(chunk_text),
                source_sha,
                fetched_at,
                commit_sha,
                1,
            ),
        )
        connection.execute(
            """
            INSERT INTO chunk_spans(chunk_uid, source_anchor, start_offset, end_offset, span_order)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                chunk_uid,
                f"https://doc.rust-lang.org/core/{row_marker}.html#{anchor}",
                0,
                len(chunk_text),
                1,
            ),
        )

        connection.execute(
            """
            INSERT INTO table1_rows(row_node_id, row_idx, row_marker, table_ref, requirement_text)
            VALUES(?, ?, ?, ?, ?)
            """,
            (row_node_id, row_idx, row_marker, "ISO 26262-6:2018 Table 1", requirement_text),
        )
        for order, term in enumerate(row_terms, start=1):
            connection.execute(
                """
                INSERT INTO table1_row_profile_terms(row_node_id, term_order, term, term_source)
                VALUES(?, ?, ?, ?)
                """,
                (row_node_id, int(order), term, "core-docs-rustdoc-v1"),
            )

    connection.execute("DELETE FROM chunks_fts")
    connection.execute(
        """
        INSERT INTO chunks_fts(chunk_uid, section_id, section_heading, chunk_text)
        SELECT c.chunk_uid, c.section_id, COALESCE(s.heading, ''), c.clean_text
        FROM chunks AS c
        LEFT JOIN sections AS s ON s.section_id = c.section_id
        ORDER BY c.chunk_uid ASC
        """
    )


def run_core_docs_build(*, args: Namespace, root: Path) -> dict[str, object]:
    db_path = (root / str(args.db_path)).resolve()
    extractor_db = Path(str(args.extractor_db)).expanduser().resolve()
    table_node_id = str(getattr(args, "table_node_id", DEFAULT_TABLE_NODE_ID))
    commit_sha = str(getattr(args, "reference_revision", "") or "").strip() or "core-docs-local"
    fetched_at = _utc_now()
    snapshot_id = f"core-docs-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"

    table_rows = _resolve_table1_rows(extractor_db=extractor_db, table_node_id=table_node_id)
    if not table_rows:
        raise RuntimeError("No Table 1 rows found for core_docs build")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    connection = sqlite3.connect(db_path)
    try:
        initialize_schema(connection)
        connection.commit()
    finally:
        connection.close()

    latest_migration_id, _ = apply_pending_migrations(db_path, root=root)

    connection = sqlite3.connect(db_path)
    try:
        _insert_core_docs_payload(
            connection,
            snapshot_id=snapshot_id,
            commit_sha=commit_sha,
            fetched_at=fetched_at,
            table_rows=table_rows,
        )
        connection.commit()
    finally:
        connection.close()

    source_state = compute_source_state_from_db(db_path)
    model_fingerprint = canonical_json_hash(
        {
            "embed_model_id": str(getattr(args, "embedding_model_id", DEFAULT_EMBEDDING_MODEL_ID)),
            "reranker_model_id": str(getattr(args, "reranker_model_id", DEFAULT_RERANKER_MODEL_ID)),
            "embedding_dim": int(getattr(args, "embedding_dim", DEFAULT_EMBEDDING_DIM)),
        }
    )

    fingerprint = record_pipeline_run(
        db_path=db_path,
        run_id=f"build::{snapshot_id}",
        corpus="core_docs",
        source_state=source_state,
        schema_migration_id=latest_migration_id,
        ingest_strategy="core_docs_rustdoc_v1",
        ingest_strategy_version="1",
        ingest_params={
            "target_min_tokens": int(getattr(args, "chunk_target_min_tokens", 150)),
            "target_max_tokens": int(getattr(args, "chunk_target_max_tokens", 500)),
        },
        retrieval_profile_id="core_docs_control",
        eval_policy_id="core_docs",
        model_fingerprint=model_fingerprint,
        allow_provenance_mismatch=bool(getattr(args, "allow_provenance_mismatch", False)),
    )

    return {
        "corpus": "core_docs",
        "snapshot_id": snapshot_id,
        "db_path": str(db_path),
        "rows": len(table_rows),
        "pipeline_fingerprint": fingerprint,
    }
