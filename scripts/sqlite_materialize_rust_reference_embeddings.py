#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from semantic_backend_client import SemanticBackendConfig, embed_texts
from sqlite_query_guardrails import GuardrailError
from sqlite_query_rust_reference import (
    ModeExecutionError,
    RetrievalContractProfile,
    _annotate_rows_with_row_markers,
    _ensure_embedding_cache_table,
    _filter_rows_by_row_marker,
    _load_embedding_cache,
    _load_statement_corpus,
    _load_table1_row_requirements,
    _resolve_retrieval_contract_profile,
    _sha256_text,
    _with_semantic_retries,
)

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def _count_corpus_rows(db_path: Path, retrieval_contract: RetrievalContractProfile) -> int:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    try:
        if retrieval_contract.corpus_query_id == "chunk_corpus_v1_all":
            return int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        return int(connection.execute("SELECT COUNT(*) FROM statements").fetchone()[0])
    finally:
        connection.close()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _resolve_progress_log_path(root: Path, raw_path: str) -> Path:
    candidate = str(raw_path).strip()
    if candidate:
        path = Path(candidate)
        if not path.is_absolute():
            path = (root / path).resolve()
        return path

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return (
        root
        / ".cache"
        / "sqlite_kb"
        / "reports"
        / "rust_reference"
        / f"materialize_progress_{stamp}.jsonl"
    ).resolve()


def _append_progress_event(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True))
        handle.write("\n")


def _progress_metrics(
    *,
    started_monotonic: float,
    created: int,
    baseline_cached: int,
    target_rows: int,
) -> dict[str, Any]:
    elapsed_sec = max(0.001, float(time.perf_counter() - started_monotonic))
    cached_now = int(baseline_cached + created)
    remaining = max(0, int(target_rows - cached_now))
    rows_per_min = (float(created) * 60.0) / elapsed_sec
    eta_min: float | None = None
    if rows_per_min > 0.0:
        eta_min = float(remaining) / rows_per_min
    return {
        "elapsed_sec": round(elapsed_sec, 3),
        "cached_now": cached_now,
        "remaining": remaining,
        "rows_per_min": round(rows_per_min, 3),
        "eta_min": None if eta_min is None else round(eta_min, 3),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize statement embeddings into rust_reference.sqlite"
    )
    parser.add_argument(
        "--db-path",
        default=".cache/sqlite_kb/current/rust_reference.sqlite",
        help="Path to rust_reference sqlite file",
    )
    parser.add_argument(
        "--contract-path",
        default="config/sqlite_query_contracts/rust_reference_chunk.yaml",
        help="Path to rust_reference query contract",
    )
    parser.add_argument(
        "--query-log-root",
        default=".cache/sqlite_kb/query_logs/rust_reference",
        help="Directory used for query audit logs",
    )
    parser.add_argument(
        "--row-marker",
        default="",
        help="Optional Table 1 row marker filter",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Embedding batch size",
    )
    parser.add_argument(
        "--semantic-base-url",
        default=os.environ.get("RUST_REF_TEI_BASE_URL", "http://127.0.0.1:8080"),
        help="Fallback semantic backend base URL",
    )
    parser.add_argument(
        "--semantic-embed-base-url",
        default=os.environ.get("RUST_REF_TEI_EMBED_BASE_URL", "http://127.0.0.1:8080"),
        help="Optional embedding backend base URL override",
    )
    parser.add_argument(
        "--semantic-rerank-base-url",
        default=os.environ.get("RUST_REF_TEI_RERANK_BASE_URL", "http://127.0.0.1:8081"),
        help="Optional reranker backend base URL override",
    )
    parser.add_argument(
        "--embed-model-id",
        default=os.environ.get("RUST_REF_EMBED_MODEL_ID", "Qwen/Qwen3-Embedding-4B"),
        help="Embedding model identifier",
    )
    parser.add_argument(
        "--reranker-model-id",
        default=os.environ.get("RUST_REF_RERANK_MODEL_ID", "BAAI/bge-reranker-v2-m3"),
        help="Reranker model identifier metadata",
    )
    parser.add_argument(
        "--semantic-timeout-sec",
        type=float,
        default=float(os.environ.get("RUST_REF_SEMANTIC_TIMEOUT_SEC", "60.0")),
        help="Semantic backend timeout per HTTP call",
    )
    parser.add_argument(
        "--semantic-retries",
        type=int,
        default=0,
        help="Retry count for embedding calls",
    )
    parser.add_argument(
        "--allow-partial-corpus",
        action="store_true",
        help=(
            "Allow scoped/partial corpus materialization without full-corpus parity checks "
            "(for local experimentation)"
        ),
    )
    parser.add_argument(
        "--progress-log-path",
        default="",
        help=(
            "Path to JSONL progress log file. Defaults to "
            ".cache/sqlite_kb/reports/rust_reference/materialize_progress_<UTC>.jsonl"
        ),
    )
    parser.add_argument(
        "--progress-interval-sec",
        type=float,
        default=60.0,
        help="Minimum seconds between progress events (final batch always logs)",
    )
    return parser.parse_args()


def _persist_rows(
    db_path: Path,
    retrieval_contract: RetrievalContractProfile,
    model_id: str,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return

    _ensure_embedding_cache_table(db_path, retrieval_contract)
    import sqlite3

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


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]

    db_path = (root / args.db_path).resolve()
    contract_path = (root / args.contract_path).resolve()
    query_log_root = (root / args.query_log_root).resolve()
    retrieval_contract = _resolve_retrieval_contract_profile(contract_path)

    config = SemanticBackendConfig(
        base_url=str(args.semantic_base_url),
        embed_model_id=str(args.embed_model_id),
        reranker_model_id=str(args.reranker_model_id),
        timeout_sec=float(args.semantic_timeout_sec),
        embed_base_url=(str(args.semantic_embed_base_url).strip() or None),
        rerank_base_url=(str(args.semantic_rerank_base_url).strip() or None),
    )

    started = time.perf_counter()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    progress_log_path: Path | None = None
    progress_interval_sec = max(1.0, float(args.progress_interval_sec))
    expected_corpus_count = 0
    require_full_corpus = False
    target_corpus_count = 0
    cached: dict[str, list[float]] = {}
    cached_after: dict[str, list[float]] = {}
    created = 0
    corpus_rows: list[dict[str, Any]] = []
    try:
        progress_log_path = _resolve_progress_log_path(root, str(args.progress_log_path))

        corpus_rows = _load_statement_corpus(
            db_path=db_path,
            contract_path=contract_path,
            query_log_root=query_log_root,
            retrieval_contract=retrieval_contract,
        )
        expected_corpus_count = _count_corpus_rows(db_path, retrieval_contract)

        scoped_row_marker = str(args.row_marker).strip().lower()
        if scoped_row_marker:
            row_profiles = _load_table1_row_requirements(
                db_path=db_path,
                contract_path=contract_path,
                query_log_root=query_log_root,
                retrieval_contract=retrieval_contract,
            )
            _annotate_rows_with_row_markers(corpus_rows, row_profiles)
            corpus_rows = _filter_rows_by_row_marker(corpus_rows, scoped_row_marker)

        require_full_corpus = (not scoped_row_marker) and (not bool(args.allow_partial_corpus))
        target_corpus_count = expected_corpus_count if require_full_corpus else len(corpus_rows)
        if require_full_corpus and len(corpus_rows) != expected_corpus_count:
            raise GuardrailError(
                "Full corpus materialization coverage mismatch: "
                f"loaded_rows={len(corpus_rows)} expected_rows={expected_corpus_count}. "
                "Check corpus contract and paging logic."
            )

        cached = _load_embedding_cache(
            db_path=db_path,
            retrieval_contract=retrieval_contract,
            model_id=config.embed_model_id,
            corpus_rows=corpus_rows,
        )
        missing_rows = [row for row in corpus_rows if str(row["statement_id"]) not in cached]

        _append_progress_event(
            progress_log_path,
            {
                "event": "start",
                "timestamp": _utc_now(),
                "run_id": run_id,
                "embed_model_id": config.embed_model_id,
                "row_marker": scoped_row_marker,
                "require_full_corpus": require_full_corpus,
                "total_statement_rows": len(corpus_rows),
                "target_statement_rows": target_corpus_count,
                "total_corpus_rows": len(corpus_rows),
                "target_corpus_rows": target_corpus_count,
                "already_cached": len(cached),
                "missing_before": len(missing_rows),
                "db_path": str(db_path),
                "corpus_query_id": retrieval_contract.corpus_query_id,
            },
        )

        last_progress_emit = time.perf_counter()
        for offset in range(0, len(missing_rows), max(1, int(args.batch_size))):
            batch_rows = missing_rows[offset : offset + max(1, int(args.batch_size))]
            texts = [str(row["statement_text"]) for row in batch_rows]
            vectors = _with_semantic_retries(
                "statement embedding",
                int(args.semantic_retries),
                lambda texts=texts: embed_texts(config, texts),
            )
            payload_rows: list[dict[str, Any]] = []
            for row, vector in zip(batch_rows, vectors, strict=False):
                normalized = [float(value) for value in vector]
                norm = sum(value * value for value in normalized) ** 0.5
                payload_rows.append(
                    {
                        "statement_id": str(row["statement_id"]),
                        "text_sha256": _sha256_text(str(row["statement_text"]).lower()),
                        "embedding": normalized,
                        "vector_norm": float(norm),
                        "source_fetched_at": str(row.get("source_fetched_at", "")),
                    }
                )

            _persist_rows(db_path, retrieval_contract, config.embed_model_id, payload_rows)
            created += len(payload_rows)

            now = time.perf_counter()
            batch_index = (offset // max(1, int(args.batch_size))) + 1
            batch_count = (len(missing_rows) + max(1, int(args.batch_size)) - 1) // max(
                1, int(args.batch_size)
            )
            is_final_batch = offset + len(batch_rows) >= len(missing_rows)
            if is_final_batch or (now - last_progress_emit) >= progress_interval_sec:
                metrics = _progress_metrics(
                    started_monotonic=started,
                    created=created,
                    baseline_cached=len(cached),
                    target_rows=target_corpus_count,
                )
                _append_progress_event(
                    progress_log_path,
                    {
                        "event": "progress",
                        "timestamp": _utc_now(),
                        "run_id": run_id,
                        "embed_model_id": config.embed_model_id,
                        "row_marker": scoped_row_marker,
                        "batch_index": batch_index,
                        "batch_count": batch_count,
                        "batch_size": len(batch_rows),
                        "created": created,
                        "target_statement_rows": target_corpus_count,
                        "target_corpus_rows": target_corpus_count,
                        **metrics,
                    },
                )
                last_progress_emit = now

        cached_after = _load_embedding_cache(
            db_path=db_path,
            retrieval_contract=retrieval_contract,
            model_id=config.embed_model_id,
            corpus_rows=corpus_rows,
        )
        if require_full_corpus and len(cached_after) != expected_corpus_count:
            missing = expected_corpus_count - len(cached_after)
            raise ModeExecutionError(
                code="SEMANTIC_INDEX_INCOMPLETE",
                message=(
                    "Semantic embedding materialization incomplete for full corpus: "
                    f"cached_rows={len(cached_after)} expected_rows={expected_corpus_count} "
                    f"missing_rows={max(0, missing)} model_id={config.embed_model_id}"
                ),
            )

    except (GuardrailError, ModeExecutionError, OSError, RuntimeError) as exc:
        if progress_log_path is not None:
            metrics = _progress_metrics(
                started_monotonic=started,
                created=created,
                baseline_cached=len(cached),
                target_rows=max(target_corpus_count, len(corpus_rows)),
            )
            _append_progress_event(
                progress_log_path,
                {
                    "event": "failed",
                    "timestamp": _utc_now(),
                    "run_id": run_id,
                    "embed_model_id": config.embed_model_id,
                    "row_marker": str(args.row_marker).strip().lower(),
                    "created": created,
                    "target_statement_rows": max(target_corpus_count, len(corpus_rows)),
                    "target_corpus_rows": max(target_corpus_count, len(corpus_rows)),
                    "error": str(exc),
                    **metrics,
                },
            )
        print(f"[materialize-rust-reference-embeddings][error] {exc}")
        return EXIT_RUNTIME_FAIL

    duration_ms = (time.perf_counter() - started) * 1000.0
    if progress_log_path is not None:
        metrics = _progress_metrics(
            started_monotonic=started,
            created=created,
            baseline_cached=len(cached),
            target_rows=target_corpus_count,
        )
        _append_progress_event(
            progress_log_path,
            {
                "event": "completed",
                "timestamp": _utc_now(),
                "run_id": run_id,
                "embed_model_id": config.embed_model_id,
                "row_marker": str(args.row_marker).strip().lower(),
                "created": created,
                "target_statement_rows": target_corpus_count,
                "target_corpus_rows": target_corpus_count,
                "cached_after": len(cached_after),
                **metrics,
            },
        )

    summary = {
        "db_path": str(db_path),
        "embed_model_id": config.embed_model_id,
        "row_marker": str(args.row_marker).strip().lower(),
        "total_statement_rows": len(corpus_rows),
        "expected_statement_rows": expected_corpus_count,
        "total_corpus_rows": len(corpus_rows),
        "expected_corpus_rows": expected_corpus_count,
        "corpus_query_id": retrieval_contract.corpus_query_id,
        "require_full_corpus": require_full_corpus,
        "already_cached": len(cached),
        "cached_after": len(cached_after),
        "new_embeddings": created,
        "duration_ms": round(duration_ms, 3),
        "progress_log_path": str(progress_log_path) if progress_log_path is not None else "",
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
