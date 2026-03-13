from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

from retrieval.build.persistence import insert_payload
from retrieval.build.schema import initialize_schema
from retrieval.core.provenance import apply_pending_migrations


def materialize_snapshot_db(
    *,
    db_path: Path,
    snapshot_root: Path,
    snapshot_id: str,
    commit_sha: str,
    source_fetched_at: str,
    source_url: str,
    snapshot_sha256: str,
    chapters: list[dict[str, Any]],
    documents: list[Any],
    sections: list[Any],
    statements: list[Any],
    chunks: list[Any],
    chunk_spans: list[Any],
    mechanisms: list[dict[str, Any]],
    mechanism_evidence: list[dict[str, Any]],
    table_rows: list[dict[str, Any]],
    row_verdicts: list[dict[str, Any]],
    row_mechanisms: list[dict[str, Any]],
    semantic_models: list[dict[str, Any]],
    semantic_corpus: list[dict[str, Any]],
    row_mechanism_scores: list[dict[str, Any]],
    extractor_version: str,
    build_notes: str,
    project_root: Path,
) -> tuple[str, Path]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_root.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    connection = sqlite3.connect(db_path)
    latest_migration_id = ""
    try:
        initialize_schema(connection)
        connection.commit()
        connection.close()
        latest_migration_id, _ = apply_pending_migrations(db_path, root=project_root)
        connection = sqlite3.connect(db_path)
        insert_payload(
            connection=connection,
            snapshot_id=snapshot_id,
            commit_sha=commit_sha,
            fetched_at=source_fetched_at,
            source_url=source_url,
            snapshot_sha256=snapshot_sha256,
            chapters=chapters,
            documents=documents,
            sections=sections,
            statements=statements,
            chunks=chunks,
            chunk_spans=chunk_spans,
            mechanisms=mechanisms,
            mechanism_evidence=mechanism_evidence,
            table_rows=table_rows,
            row_verdicts=row_verdicts,
            row_mechanisms=row_mechanisms,
            semantic_models=semantic_models,
            semantic_corpus=semantic_corpus,
            row_mechanism_scores=row_mechanism_scores,
            extractor_version=extractor_version,
            build_notes=build_notes,
        )
        connection.commit()
    finally:
        connection.close()

    snapshot_db_path = snapshot_root / f"{snapshot_id}.sqlite"
    shutil.copy2(db_path, snapshot_db_path)
    return latest_migration_id, snapshot_db_path
