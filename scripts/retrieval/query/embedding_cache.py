from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from typing import Protocol, cast

from retrieval.query.semantic_math import l2_norm

EMBEDDING_CACHE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS statement_embeddings (
    statement_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    vector_norm REAL NOT NULL,
    embedded_at TEXT NOT NULL,
    source_fetched_at TEXT NOT NULL,
    PRIMARY KEY(statement_id, model_id),
    FOREIGN KEY(statement_id) REFERENCES statements(statement_id)
);
CREATE INDEX IF NOT EXISTS idx_statement_embeddings_model
    ON statement_embeddings(model_id, statement_id);
"""

CHUNK_EMBEDDING_CACHE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_uid TEXT NOT NULL,
    model_id TEXT NOT NULL,
    embed_version TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    vector_norm REAL NOT NULL,
    embedded_at TEXT NOT NULL,
    source_fetched_at TEXT NOT NULL,
    PRIMARY KEY(chunk_uid, model_id, embed_version),
    FOREIGN KEY(chunk_uid) REFERENCES chunks(chunk_uid)
);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_model
    ON chunk_embeddings(model_id, chunk_uid, embed_version);
"""


class EmbeddingContractProfile(Protocol):
    @property
    def embedding_table(self) -> str: ...

    @property
    def embed_version(self) -> str: ...


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ensure_embedding_cache_table(
    db_path: Path, retrieval_contract: EmbeddingContractProfile
) -> None:
    connection = sqlite3.connect(db_path)
    try:
        if retrieval_contract.embedding_table == "chunk_embeddings":
            connection.executescript(CHUNK_EMBEDDING_CACHE_TABLE_DDL)
        else:
            connection.executescript(EMBEDDING_CACHE_TABLE_DDL)
        connection.commit()
    finally:
        connection.close()


def load_embedding_cache(
    *,
    db_path: Path,
    retrieval_contract: EmbeddingContractProfile,
    model_id: str,
    corpus_rows: list[dict[str, Any]],
) -> dict[str, list[float]]:
    if not corpus_rows:
        return {}

    statement_ids = [str(row["statement_id"]) for row in corpus_rows]
    expected_hash_by_id = {
        str(row["statement_id"]): str(row.get("text_sha256", "")) for row in corpus_rows
    }

    embedding_by_id: dict[str, list[float]] = {}
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    try:
        connection.row_factory = sqlite3.Row
        chunk_size = 800
        for offset in range(0, len(statement_ids), chunk_size):
            chunk = statement_ids[offset : offset + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            if retrieval_contract.embedding_table == "chunk_embeddings":
                sql = (
                    "SELECT chunk_uid AS cache_id, text_sha256, vector_json "
                    "FROM chunk_embeddings "
                    "WHERE model_id = ? AND embed_version = ? AND chunk_uid IN ("
                    + placeholders
                    + ")"
                )
                rows = connection.execute(
                    sql,
                    [model_id, retrieval_contract.embed_version, *chunk],
                ).fetchall()
            else:
                sql = (
                    "SELECT statement_id AS cache_id, text_sha256, vector_json "
                    "FROM statement_embeddings "
                    "WHERE model_id = ? AND statement_id IN (" + placeholders + ")"
                )
                rows = connection.execute(sql, [model_id, *chunk]).fetchall()
            for row in rows:
                statement_id = str(row["cache_id"])
                if str(row["text_sha256"]) != expected_hash_by_id.get(statement_id, ""):
                    continue
                vector_payload = json.loads(str(row["vector_json"]))
                if isinstance(vector_payload, list):
                    embedding_by_id[statement_id] = [float(value) for value in vector_payload]
    finally:
        connection.close()

    return embedding_by_id


def persist_embedding_cache(
    *,
    db_path: Path,
    retrieval_contract: EmbeddingContractProfile,
    model_id: str,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return

    ensure_embedding_cache_table(db_path, retrieval_contract)
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
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
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
                        json.dumps(row["embedding"], sort_keys=False),
                        float(
                            l2_norm(
                                [
                                    float(value)
                                    for value in cast(list[Any], row.get("embedding", []))
                                ]
                            )
                        ),
                        datetime.now(UTC).isoformat(timespec="seconds"),
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
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
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
                        json.dumps(row["embedding"], sort_keys=False),
                        float(
                            l2_norm(
                                [
                                    float(value)
                                    for value in cast(list[Any], row.get("embedding", []))
                                ]
                            )
                        ),
                        datetime.now(UTC).isoformat(timespec="seconds"),
                        str(row.get("source_fetched_at", "")),
                    )
                    for row in rows
                ],
            )
        connection.commit()
    finally:
        connection.close()
