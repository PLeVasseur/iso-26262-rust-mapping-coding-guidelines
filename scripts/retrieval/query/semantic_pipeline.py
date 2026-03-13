from __future__ import annotations

import time
from typing import Any, Protocol

from retrieval.query.backend_retry import with_semantic_retries
from retrieval.query.embedding_cache import load_embedding_cache, persist_embedding_cache
from retrieval.query.errors import ModeExecutionError
from retrieval.query.semantic_math import cosine_similarity, min_max_normalize
from semantic_backend_client import SemanticBackendConfig, embed_texts, rerank_texts


class SemanticContractProfileLike(Protocol):
    @property
    def embedding_table(self) -> str: ...

    @property
    def embed_version(self) -> str: ...


def semantic_candidates(
    *,
    db_path: Any,
    retrieval_contract: SemanticContractProfileLike,
    config: SemanticBackendConfig,
    retries: int,
    query_text: str,
    corpus_rows: list[dict[str, Any]],
    top_k: int,
    candidate_limit: int,
    persist_cache: bool,
    allow_online_corpus_embedding: bool,
    retry_events: list[dict[str, Any]] | None = None,
    timing: dict[str, float] | None = None,
    workload: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    if not corpus_rows:
        return []

    query_embed_started = time.perf_counter()
    query_embedding = with_semantic_retries(
        "query embedding",
        retries,
        lambda: embed_texts(config, [query_text]),
        telemetry=retry_events,
    )[0]
    if timing is not None:
        timing["semantic_embed_ms"] = timing.get("semantic_embed_ms", 0.0) + (
            (time.perf_counter() - query_embed_started) * 1000.0
        )

    embeddings_by_statement_id = load_embedding_cache(
        db_path=db_path,
        retrieval_contract=retrieval_contract,
        model_id=config.embed_model_id,
        corpus_rows=corpus_rows,
    )

    missing_rows = [
        row for row in corpus_rows if str(row["statement_id"]) not in embeddings_by_statement_id
    ]
    if missing_rows:
        if not allow_online_corpus_embedding:
            sample_ids = [str(row["statement_id"]) for row in missing_rows[:5]]
            raise ModeExecutionError(
                code="SEMANTIC_INDEX_INCOMPLETE",
                message=(
                    f"Semantic index incomplete for model {config.embed_model_id}: "
                    f"missing_embeddings={len(missing_rows)} sample_statement_ids={sample_ids}. "
                    "Run 'sqlite_kb.py materialize --corpus <corpus>' first "
                    "or set --allow-online-corpus-embedding for local experimentation."
                ),
            )

        batch_size = 32
        persisted_payloads: list[dict[str, Any]] = []
        for offset in range(0, len(missing_rows), batch_size):
            batch_rows = missing_rows[offset : offset + batch_size]
            batch_texts = [str(row["statement_text"]) for row in batch_rows]
            statement_embed_started = time.perf_counter()
            vectors = with_semantic_retries(
                "statement embedding",
                retries,
                lambda batch_texts=batch_texts: embed_texts(config, batch_texts),
                telemetry=retry_events,
            )
            if timing is not None:
                timing["semantic_embed_ms"] = timing.get("semantic_embed_ms", 0.0) + (
                    (time.perf_counter() - statement_embed_started) * 1000.0
                )
            if len(vectors) != len(batch_rows):
                raise ModeExecutionError(
                    code="SEMANTIC_BACKEND_UNAVAILABLE",
                    message=(
                        "Semantic embedding cardinality mismatch "
                        f"({len(vectors)} != {len(batch_rows)})"
                    ),
                )

            for row, vector in zip(batch_rows, vectors, strict=False):
                statement_id = str(row["statement_id"])
                embeddings_by_statement_id[statement_id] = [float(value) for value in vector]
                persisted_payloads.append(
                    {
                        "statement_id": statement_id,
                        "text_sha256": str(row.get("text_sha256", "")),
                        "embedding": [float(value) for value in vector],
                        "source_fetched_at": str(row.get("source_fetched_at", "")),
                    }
                )

        if persist_cache:
            persist_embedding_cache(
                db_path=db_path,
                retrieval_contract=retrieval_contract,
                model_id=config.embed_model_id,
                rows=persisted_payloads,
            )

    score_started = time.perf_counter()
    scored_rows: list[dict[str, Any]] = []
    semantic_scores: list[float] = []
    for row in corpus_rows:
        statement_id = str(row["statement_id"])
        if statement_id not in embeddings_by_statement_id:
            continue
        vector = embeddings_by_statement_id[statement_id]
        semantic_score = cosine_similarity(query_embedding, vector)
        enriched = dict(row)
        enriched["semantic_score_raw"] = semantic_score
        scored_rows.append(enriched)
        semantic_scores.append(semantic_score)

    if not scored_rows:
        return []

    normalized_semantic = min_max_normalize(semantic_scores)
    for row, norm_score in zip(scored_rows, normalized_semantic, strict=False):
        row["semantic_score"] = float(norm_score)

    scored_rows.sort(
        key=lambda row: (
            -float(row["semantic_score"]),
            str(row["statement_id"]),
        )
    )

    semantic_pool_limit = max(top_k, min(int(candidate_limit), len(scored_rows)))
    semantic_pool = scored_rows[:semantic_pool_limit]

    rerank_pool_limit = min(len(semantic_pool), max(top_k * 8, 64))
    rerank_pool = semantic_pool[:rerank_pool_limit]
    rerank_texts_input = [str(row["statement_text"]) for row in rerank_pool]
    if timing is not None:
        timing["semantic_score_ms"] = timing.get("semantic_score_ms", 0.0) + (
            (time.perf_counter() - score_started) * 1000.0
        )
    if workload is not None:
        workload["semantic_pool_size"] = int(len(semantic_pool))
        workload["rerank_pool_size"] = int(len(rerank_pool))
        workload["rerank_doc_count"] = int(len(rerank_texts_input))

    rerank_started = time.perf_counter()
    reranker_scores_raw = with_semantic_retries(
        "reranker scoring",
        retries,
        lambda: rerank_texts(
            config=config,
            query_text=query_text,
            documents=rerank_texts_input,
        ),
        telemetry=retry_events,
    )
    if timing is not None:
        timing["rerank_ms"] = timing.get("rerank_ms", 0.0) + (
            (time.perf_counter() - rerank_started) * 1000.0
        )
    reranker_scores = min_max_normalize([float(value) for value in reranker_scores_raw])

    reranker_by_id: dict[str, float] = {}
    for row, rerank_score in zip(rerank_pool, reranker_scores, strict=False):
        reranker_by_id[str(row["statement_id"])] = float(rerank_score)

    for row in scored_rows:
        reranker_score = reranker_by_id.get(str(row["statement_id"]), 0.0)
        row["reranker_score"] = reranker_score
        row["relevance_score"] = (0.45 * float(row["semantic_score"])) + (0.55 * reranker_score)

    scored_rows.sort(
        key=lambda row: (
            -float(row["relevance_score"]),
            -float(row["reranker_score"]),
            str(row["statement_id"]),
        )
    )
    return scored_rows
