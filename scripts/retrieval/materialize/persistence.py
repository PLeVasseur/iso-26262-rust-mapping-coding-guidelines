from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from retrieval.query.contracts import RetrievalContractProfile
from retrieval.query.embedding_cache import (
    ensure_embedding_cache_table as _ensure_embedding_cache_table,
    sha256_text as _sha256_text,
)


def count_corpus_rows(db_path: Path, retrieval_contract: RetrievalContractProfile) -> int:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    try:
        if retrieval_contract.corpus_query_id == "chunk_corpus_v1_all":
            return int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        return int(connection.execute("SELECT COUNT(*) FROM statements").fetchone()[0])
    finally:
        connection.close()


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def row_text_sha(row: dict[str, Any]) -> str:
    return _sha256_text(str(row.get("statement_text", "")).lower())


def dedupe_key(row: dict[str, Any], text_sha: str) -> tuple[str, str, str, str]:
    return (
        text_sha,
        str(row.get("target_triple", "") or ""),
        str(row.get("target_env", "") or ""),
        str(row.get("cfg_signature_sha256", "") or ""),
    )


def load_embedding_key_cache(
    db_path: Path,
    retrieval_contract: RetrievalContractProfile,
    *,
    model_id: str,
) -> dict[tuple[str, str, str, str], tuple[list[float], float]]:
    cache: dict[tuple[str, str, str, str], tuple[list[float], float]] = {}
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA busy_timeout = 1500")
        if retrieval_contract.embedding_table == "chunk_embeddings":
            has_core_docs_meta = table_exists(connection, "core_docs_chunk_metadata")
            if has_core_docs_meta:
                rows = connection.execute(
                    """
                    SELECT
                        e.text_sha256,
                        COALESCE(m.target_triple, ''),
                        COALESCE(m.target_env, ''),
                        COALESCE(m.cfg_signature_sha256, ''),
                        e.vector_json,
                        e.vector_norm
                    FROM chunk_embeddings AS e
                    LEFT JOIN core_docs_chunk_metadata AS m ON m.chunk_uid = e.chunk_uid
                    WHERE e.model_id = ? AND e.embed_version = ?
                    """,
                    (model_id, retrieval_contract.embed_version),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT text_sha256, '', '', '', vector_json, vector_norm
                    FROM chunk_embeddings
                    WHERE model_id = ? AND embed_version = ?
                    """,
                    (model_id, retrieval_contract.embed_version),
                ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT text_sha256, '', '', '', vector_json, vector_norm
                FROM statement_embeddings
                WHERE model_id = ?
                """,
                (model_id,),
            ).fetchall()
        for text_sha, target_triple, target_env, cfg_sha, vector_json, vector_norm in rows:
            key = (str(text_sha), str(target_triple), str(target_env), str(cfg_sha))
            cache[key] = (
                [float(value) for value in json.loads(str(vector_json))],
                float(vector_norm),
            )
    finally:
        connection.close()
    return cache


def persist_rows(
    db_path: Path,
    retrieval_contract: RetrievalContractProfile,
    model_id: str,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return

    _ensure_embedding_cache_table(db_path, retrieval_contract)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA busy_timeout = 1500")
        if retrieval_contract.embedding_table == "chunk_embeddings":
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
                ) VALUES(?, ?, ?, ?, ?, ?, datetime('now'), ?)
                ON CONFLICT(chunk_uid, model_id, embed_version)
                DO UPDATE SET
                    text_sha256 = excluded.text_sha256,
                    vector_json = excluded.vector_json,
                    vector_norm = excluded.vector_norm,
                    embedded_at = excluded.embedded_at,
                    source_fetched_at = excluded.source_fetched_at
                """,
                [
                    (
                        str(row["statement_id"]),
                        model_id,
                        retrieval_contract.embed_version,
                        str(row["text_sha256"]),
                        json.dumps(row["embedding"]),
                        float(row["vector_norm"]),
                        str(row.get("source_fetched_at", "")),
                    )
                    for row in rows
                ],
            )
        else:
            connection.executemany(
                """
                INSERT INTO statement_embeddings(
                    statement_id,
                    model_id,
                    text_sha256,
                    vector_json,
                    vector_norm,
                    embedded_at,
                    source_fetched_at
                ) VALUES(?, ?, ?, ?, ?, datetime('now'), ?)
                ON CONFLICT(statement_id, model_id)
                DO UPDATE SET
                    text_sha256 = excluded.text_sha256,
                    vector_json = excluded.vector_json,
                    vector_norm = excluded.vector_norm,
                    embedded_at = excluded.embedded_at,
                    source_fetched_at = excluded.source_fetched_at
                """,
                [
                    (
                        str(row["statement_id"]),
                        model_id,
                        str(row["text_sha256"]),
                        json.dumps(row["embedding"]),
                        float(row["vector_norm"]),
                        str(row.get("source_fetched_at", "")),
                    )
                    for row in rows
                ],
            )
        connection.commit()
    finally:
        connection.close()
