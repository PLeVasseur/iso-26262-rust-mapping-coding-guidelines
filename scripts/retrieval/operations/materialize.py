#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from retrieval.operations.query import (
    _load_statement_corpus,
    _load_table1_row_requirements,
    _resolve_retrieval_contract_profile,
)
from retrieval.materialize.cli import parse_args
from retrieval.materialize.persistence import (
    count_corpus_rows as _count_corpus_rows,
    dedupe_key as _dedupe_key,
    load_embedding_key_cache as _load_embedding_key_cache,
    persist_rows as _persist_rows,
    row_text_sha as _row_text_sha,
)
from retrieval.materialize.progress import (
    append_progress_event as _append_progress_event,
    progress_metrics as _progress_metrics,
    resolve_progress_log_path as _resolve_progress_log_path,
    utc_now as _utc_now,
)
from retrieval.query.backend_retry import with_semantic_retries as _with_semantic_retries
from retrieval.query.contracts import RetrievalContractProfile
from retrieval.query.embedding_cache import (
    load_embedding_cache as _load_embedding_cache,
)
from retrieval.query.errors import ModeExecutionError
from retrieval.query.row_markers import (
    annotate_rows_with_row_markers as _annotate_rows_with_row_markers,
)
from retrieval.query.row_markers import (
    filter_rows_by_row_marker as _filter_rows_by_row_marker,
)
from semantic_backend_client import SemanticBackendConfig, embed_texts
from sqlite_query_guardrails import GuardrailError

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def _health_device(base_url: str) -> str:
    endpoint = str(base_url).rstrip("/") + "/health"
    try:
        with urlopen(endpoint, timeout=5.0) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Failed to fetch backend health from {endpoint}: {exc}") from exc
    device = str(payload.get("device", "")).strip().lower()
    if not device:
        raise RuntimeError(f"Backend health endpoint {endpoint} did not report device")
    return device


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[3]

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

        effective_embed_device = _health_device(str(config.embed_base_url or config.base_url))
        effective_rerank_device = _health_device(str(config.rerank_base_url or config.base_url))
        if bool(args.require_mps) and effective_embed_device != "mps":
            cpu_override = bool(args.allow_cpu_fallback) and effective_embed_device == "cpu"
            mismatch = {
                "requested": {"embed": "mps", "rerank": "mps"},
                "effective": {
                    "embed": effective_embed_device,
                    "rerank": effective_rerank_device,
                },
                "allow_cpu_fallback": bool(args.allow_cpu_fallback),
            }
            if not cpu_override:
                raise RuntimeError(
                    "Effective backend device mismatch for materialize (fail-closed): "
                    + json.dumps(mismatch, sort_keys=True)
                )
            warning_payload = {
                "status": "cpu_fallback_override",
                "requested": mismatch["requested"],
                "effective": mismatch["effective"],
                "note": (
                    "CPU fallback override active; throughput and quality comparisons may be "
                    "non-comparable without explicit approval"
                ),
            }
            print(
                "[materialize-rust-reference-embeddings][warning] "
                + json.dumps(warning_payload, sort_keys=True),
                file=sys.stderr,
            )
        elif effective_embed_device == "cpu":
            warning_payload = {
                "status": "cpu_effective_device",
                "requested": {"embed": "mps", "rerank": "mps"},
                "effective": {
                    "embed": effective_embed_device,
                    "rerank": effective_rerank_device,
                },
                "note": (
                    "Embedding backend is running on CPU; throughput may be significantly slower"
                ),
            }
            print(
                "[materialize-rust-reference-embeddings][warning] "
                + json.dumps(warning_payload, sort_keys=True),
                file=sys.stderr,
            )

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

        if int(args.max_rows) > 0:
            corpus_rows = corpus_rows[: int(args.max_rows)]

        require_full_corpus = (
            (not scoped_row_marker)
            and (not bool(args.allow_partial_corpus))
            and int(args.max_rows) <= 0
        )
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
        key_cache = _load_embedding_key_cache(
            db_path,
            retrieval_contract,
            model_id=config.embed_model_id,
        )
        missing_rows = [row for row in corpus_rows if str(row["statement_id"]) not in cached]

        missing_grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for row in missing_rows:
            text_sha = _row_text_sha(row)
            key = _dedupe_key(row, text_sha)
            missing_grouped.setdefault(key, []).append(row)

        unique_missing_keys = list(missing_grouped.keys())
        duplicate_text_count = max(0, len(missing_rows) - len(unique_missing_keys))
        dedupe_ratio = (
            float(duplicate_text_count) / float(max(1, len(missing_rows))) if missing_rows else 0.0
        )

        embed_calls_made = 0

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
                "unique_text_count": len(unique_missing_keys),
                "duplicate_text_count": duplicate_text_count,
                "dedupe_ratio": round(dedupe_ratio, 6),
                "db_path": str(db_path),
                "corpus_query_id": retrieval_contract.corpus_query_id,
                "effective_embed_device": effective_embed_device,
                "effective_rerank_device": effective_rerank_device,
            },
        )

        last_progress_emit = time.perf_counter()
        batch_size = max(1, int(args.batch_size))
        for offset in range(0, len(unique_missing_keys), batch_size):
            batch_keys = unique_missing_keys[offset : offset + batch_size]
            pending_keys = [key for key in batch_keys if key not in key_cache]
            if pending_keys:
                texts = [str(missing_grouped[key][0]["statement_text"]) for key in pending_keys]
                vectors = _with_semantic_retries(
                    "statement embedding",
                    int(args.semantic_retries),
                    lambda texts=texts: embed_texts(config, texts),
                )
                embed_calls_made += len(pending_keys)
                for key, vector in zip(pending_keys, vectors, strict=False):
                    normalized = [float(value) for value in vector]
                    norm = sum(value * value for value in normalized) ** 0.5
                    key_cache[key] = (normalized, float(norm))

            payload_rows: list[dict[str, Any]] = []
            for key in batch_keys:
                vector, norm = key_cache[key]
                text_sha = key[0]
                for row in missing_grouped[key]:
                    payload_rows.append(
                        {
                            "statement_id": str(row["statement_id"]),
                            "text_sha256": text_sha,
                            "embedding": vector,
                            "vector_norm": float(norm),
                            "source_fetched_at": str(row.get("source_fetched_at", "")),
                        }
                    )

            _persist_rows(db_path, retrieval_contract, config.embed_model_id, payload_rows)
            created += len(payload_rows)

            now = time.perf_counter()
            batch_index = (offset // batch_size) + 1
            batch_count = (len(unique_missing_keys) + batch_size - 1) // batch_size
            is_final_batch = offset + len(batch_keys) >= len(unique_missing_keys)
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
                        "batch_size": len(batch_keys),
                        "embed_calls_made": embed_calls_made,
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
        "unique_text_count": len(unique_missing_keys),
        "duplicate_text_count": duplicate_text_count,
        "dedupe_ratio": round(dedupe_ratio, 6),
        "embed_calls_made": embed_calls_made,
        "effective_embed_device": effective_embed_device,
        "effective_rerank_device": effective_rerank_device,
        "duration_ms": round(duration_ms, 3),
        "progress_log_path": str(progress_log_path) if progress_log_path is not None else "",
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
