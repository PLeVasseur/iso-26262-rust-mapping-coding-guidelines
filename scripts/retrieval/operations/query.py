#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from retrieval.core.engine import build_runtime_config
from retrieval.core.profile import (
    DEFAULT_HYBRID_RRF_K,
    HYBRID_CANDIDATE_POLICY_LEGACY,
    HYBRID_FUSION_WEIGHTED_V1,
)
from retrieval.core.profile_loader import (
    apply_profile_defaults,
    enforce_profile_corpus,
    load_retrieval_profile,
)
from retrieval.core.telemetry import (
    init_retrieval_timing,
    init_retrieval_workload,
)
from retrieval.core.telemetry import (
    timing_payload as core_timing_payload,
)
from retrieval.corpora.runtime_paths import resolve_corpus_runtime_paths
from retrieval.query.backend_retry import with_semantic_retries as _with_semantic_retries
from retrieval.query.cli import parse_args as parse_query_args
from retrieval.query.contracts import RetrievalContractProfile
from retrieval.query.contracts import (
    resolve_retrieval_contract_profile as _resolve_retrieval_contract_profile,
)
from retrieval.query.embedding_cache import (
    ensure_embedding_cache_table,
)
from retrieval.query.errors import ModeExecutionError
from retrieval.query.fusion_metadata import build_fusion_metadata
from retrieval.query.lexical_pipeline import load_statement_corpus as core_load_statement_corpus
from retrieval.query.lexical_pipeline import (
    load_table1_row_requirements as core_load_table1_row_requirements,
)
from retrieval.query.lexical_pipeline import run_lexical_query as core_run_lexical_query
from retrieval.query.row_markers import (
    annotate_rows_with_row_markers as _annotate_rows_with_row_markers,
)
from retrieval.query.row_markers import (
    filter_rows_by_row_marker as _filter_rows_by_row_marker,
)
from retrieval.query.row_projection import apply_abstain_policy as core_apply_abstain_policy
from retrieval.query.row_projection import build_row_projection as core_build_row_projection
from retrieval.query.mode_finalizers import finalize_lexical_like_result
from retrieval.query.mode_execution import finalize_hybrid_mode, finalize_semantic_mode
from retrieval.query.output_filters import (
    apply_corpus_row_policy as _apply_corpus_row_policy,
)
from retrieval.query.output_filters import without_score_breakdown as core_without_score_breakdown
from retrieval.query.policy_resolution import RowProjectionPolicy
from retrieval.query.policy_resolution import (
    resolve_row_projection_policy,
    row_projection_policy_from_globals as _row_projection_policy_from_globals,
)
from retrieval.query.rewrite_rules import rewrite_query_text as _rewrite_query_text
from retrieval.query.semantic_pipeline import semantic_candidates as core_semantic_candidates
from retrieval.query.review_artifacts import (
    build_review_artifact_payload,
    persist_review_artifact,
    resolve_review_artifact_path,
)
from semantic_backend_client import (
    SemanticBackendConfig,
    check_semantic_backend,
)
from sqlite_query_guardrails import GuardrailError, execute_contract_query

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3
DEFAULT_TOP_K = 20
DEFAULT_CANDIDATE_LIMIT = 5000
FULL_CORPUS_PAGE_LIMIT = 5000
DEFAULT_QUERY_REVIEW_DIR = ".cache/sqlite_kb/reports/rust_reference/query_reviews"
DEFAULT_REWRITE_RULES_PATH = "config/sqlite_query_rewrite/rust_reference_rewrite.yaml"
REVIEW_ARTIFACT_SCHEMA_VERSION = 1


SCORE_FIELDS = {
    "bm25_raw",
    "phrase_match",
    "token_overlap_count",
    "lexical_score",
    "semantic_score",
    "semantic_score_raw",
    "reranker_score",
    "relevance_score",
    "row_marker_scores",
    "rrf_score",
    "lexical_rank",
    "semantic_rank",
    "reranker_rank",
}

LEXICAL_WEIGHT = 0.30
SEMANTIC_WEIGHT = 0.25
RERANK_WEIGHT = 0.45
WEIGHTED_V2_LEXICAL_WEIGHT = 0.55
WEIGHTED_V2_SEMANTIC_WEIGHT = 0.15
WEIGHTED_V2_RERANK_WEIGHT = 0.30


def parse_args() -> argparse.Namespace:
    return parse_query_args(
        default_top_k=DEFAULT_TOP_K,
        default_candidate_limit=DEFAULT_CANDIDATE_LIMIT,
    )


def _parse_params(raw: str) -> dict[str, Any]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise GuardrailError("--params-json must decode to an object")
    return payload


def _row_identity(row: dict[str, Any]) -> str:
    candidate = str(row.get("chunk_uid", "")).strip()
    if candidate:
        return candidate
    return str(row.get("statement_id", "")).strip()


def _run_lexical_query(
    *,
    contract_path: Path,
    retrieval_contract: RetrievalContractProfile,
    query_log_root: Path,
    db_path: Path,
    corpus_rows: list[dict[str, Any]],
    row_profiles: list[dict[str, Any]],
    query_text: str,
    row_marker: str,
    row_limit: int,
) -> list[dict[str, Any]]:
    return core_run_lexical_query(
        contract_path=contract_path,
        retrieval_contract=retrieval_contract,
        query_log_root=query_log_root,
        db_path=db_path,
        corpus_rows=corpus_rows,
        row_profiles=row_profiles,
        query_text=query_text,
        row_marker=row_marker,
        row_limit=row_limit,
        row_identity=_row_identity,
    )


def _load_statement_corpus(
    *,
    db_path: Path,
    contract_path: Path,
    query_log_root: Path,
    retrieval_contract: RetrievalContractProfile | None = None,
    max_rows: int | None = None,
) -> list[dict[str, Any]]:
    profile = retrieval_contract or _resolve_retrieval_contract_profile(contract_path)
    return core_load_statement_corpus(
        db_path=db_path,
        contract_path=contract_path,
        query_log_root=query_log_root,
        retrieval_contract=profile,
        full_corpus_page_limit=FULL_CORPUS_PAGE_LIMIT,
        max_rows=max_rows,
    )


def _load_table1_row_requirements(
    *,
    db_path: Path,
    contract_path: Path,
    query_log_root: Path,
    retrieval_contract: RetrievalContractProfile | None = None,
) -> list[dict[str, Any]]:
    profile = retrieval_contract or _resolve_retrieval_contract_profile(contract_path)
    return core_load_table1_row_requirements(
        db_path=db_path,
        contract_path=contract_path,
        query_log_root=query_log_root,
        retrieval_contract=profile,
    )


def _build_semantic_config(args: argparse.Namespace) -> SemanticBackendConfig:
    return SemanticBackendConfig(
        base_url=args.semantic_base_url,
        embed_model_id=args.embed_model_id,
        reranker_model_id=args.reranker_model_id,
        timeout_sec=float(args.semantic_timeout_sec),
        embed_base_url=(str(args.semantic_embed_base_url).strip() or None),
        rerank_base_url=(str(args.semantic_rerank_base_url).strip() or None),
    )


def _semantic_candidates(
    *,
    db_path: Path,
    retrieval_contract: RetrievalContractProfile,
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
    return core_semantic_candidates(
        db_path=db_path,
        retrieval_contract=retrieval_contract,
        config=config,
        retries=retries,
        query_text=query_text,
        corpus_rows=corpus_rows,
        top_k=top_k,
        candidate_limit=candidate_limit,
        persist_cache=persist_cache,
        allow_online_corpus_embedding=allow_online_corpus_embedding,
        retry_events=retry_events,
        timing=timing,
        workload=workload,
    )


def _build_row_projection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return core_build_row_projection(rows)


def _apply_abstain_policy(
    projection: list[dict[str, Any]],
    *,
    policy: RowProjectionPolicy,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return core_apply_abstain_policy(projection, policy=policy)


def execute_retrieval_query(
    *,
    mode: str,
    db_path: Path,
    contract_path: Path,
    query_log_root: Path,
    query_text: str,
    row_marker: str,
    top_k: int,
    candidate_limit: int,
    allow_degraded: bool,
    semantic_config: SemanticBackendConfig,
    semantic_retries: int,
    persist_semantic_cache: bool,
    allow_online_corpus_embedding: bool = False,
    rewrite_mode: str = "auto",
    rewrite_rules_path: Path | None = None,
    hybrid_fusion_method: str = HYBRID_FUSION_WEIGHTED_V1,
    hybrid_rrf_k: int = DEFAULT_HYBRID_RRF_K,
    hybrid_rrf_window: int = 0,
    hybrid_lexical_floor_count: int = 0,
    hybrid_lexical_floor_share: float = 0.0,
    hybrid_candidate_policy: str = HYBRID_CANDIDATE_POLICY_LEGACY,
    hybrid_rerank_pool_size: int = 0,
    hybrid_lexical_min: int = 0,
    hybrid_semantic_min: int = 0,
    corpus: str = "",
    row_projection_policy: RowProjectionPolicy | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    semantic_retry_events: list[dict[str, Any]] = []
    row_marker = row_marker.strip().lower()
    try:
        runtime_config = build_runtime_config(
            top_k=top_k,
            candidate_limit=candidate_limit,
            hybrid_fusion_method=hybrid_fusion_method,
            hybrid_rrf_k=hybrid_rrf_k,
            hybrid_rrf_window=hybrid_rrf_window,
            hybrid_lexical_floor_count=hybrid_lexical_floor_count,
            hybrid_lexical_floor_share=hybrid_lexical_floor_share,
            hybrid_candidate_policy=hybrid_candidate_policy,
            hybrid_rerank_pool_size=hybrid_rerank_pool_size,
            hybrid_lexical_min=hybrid_lexical_min,
            hybrid_semantic_min=hybrid_semantic_min,
        )
    except ValueError as exc:
        raise GuardrailError(str(exc)) from exc

    top_k = runtime_config.top_k
    candidate_limit = runtime_config.candidate_limit
    normalized_fusion_method = runtime_config.hybrid_fusion_method
    resolved_rrf_k = runtime_config.hybrid_rrf_k
    resolved_rrf_window = runtime_config.hybrid_rrf_window
    resolved_lexical_floor_count = runtime_config.hybrid_lexical_floor_count
    resolved_lexical_floor_share = runtime_config.hybrid_lexical_floor_share
    normalized_candidate_policy = runtime_config.hybrid_candidate_policy
    resolved_row_projection_policy = row_projection_policy or _row_projection_policy_from_globals()
    resolved_hybrid_rerank_pool_size = runtime_config.hybrid_rerank_pool_size
    resolved_hybrid_lexical_min = runtime_config.hybrid_lexical_min
    resolved_hybrid_semantic_min = runtime_config.hybrid_semantic_min

    timing = init_retrieval_timing()
    workload = init_retrieval_workload()

    def _timing_payload(total_case_ms: float) -> dict[str, float]:
        return core_timing_payload(timing, total_case_ms)

    rewrite_path = rewrite_rules_path or (
        Path(__file__).resolve().parents[3] / DEFAULT_REWRITE_RULES_PATH
    )
    try:
        rewrite = _rewrite_query_text(
            query_text=query_text,
            row_marker=row_marker,
            mode=mode,
            rewrite_mode=rewrite_mode,
            rewrite_rules_path=rewrite_path,
        )
    except ValueError as exc:
        raise GuardrailError(str(exc)) from exc
    effective_query_text = str(rewrite.get("rewritten_query", query_text)).strip() or query_text

    retrieval_contract = _resolve_retrieval_contract_profile(contract_path)

    corpus_rows = _load_statement_corpus(
        db_path=db_path,
        contract_path=contract_path,
        query_log_root=query_log_root,
        retrieval_contract=retrieval_contract,
    )
    row_profiles = _load_table1_row_requirements(
        db_path=db_path,
        contract_path=contract_path,
        query_log_root=query_log_root,
        retrieval_contract=retrieval_contract,
    )

    def _run_lexical() -> list[dict[str, Any]]:
        return _run_lexical_query(
            db_path=db_path,
            contract_path=contract_path,
            query_log_root=query_log_root,
            retrieval_contract=retrieval_contract,
            corpus_rows=corpus_rows,
            row_profiles=row_profiles,
            query_text=effective_query_text,
            row_marker=row_marker,
            row_limit=candidate_limit,
        )

    score_definitions, fusion_params = build_fusion_metadata(
        normalized_fusion_method=normalized_fusion_method,
        resolved_rrf_k=resolved_rrf_k,
        resolved_rrf_window=resolved_rrf_window,
        resolved_lexical_floor_count=resolved_lexical_floor_count,
        resolved_lexical_floor_share=resolved_lexical_floor_share,
        normalized_candidate_policy=normalized_candidate_policy,
        resolved_hybrid_rerank_pool_size=resolved_hybrid_rerank_pool_size,
        resolved_hybrid_lexical_min=resolved_hybrid_lexical_min,
        resolved_hybrid_semantic_min=resolved_hybrid_semantic_min,
        lexical_weight=LEXICAL_WEIGHT,
        semantic_weight=SEMANTIC_WEIGHT,
        rerank_weight=RERANK_WEIGHT,
        weighted_v2_lexical_weight=WEIGHTED_V2_LEXICAL_WEIGHT,
        weighted_v2_semantic_weight=WEIGHTED_V2_SEMANTIC_WEIGHT,
        weighted_v2_rerank_weight=WEIGHTED_V2_RERANK_WEIGHT,
    )
    fusion_debug: dict[str, Any] = {}

    if mode == "lexical":
        lexical_started = time.perf_counter()
        lexical_rows = _run_lexical()
        timing["lexical_ms"] += (time.perf_counter() - lexical_started) * 1000.0
        return finalize_lexical_like_result(
            requested_mode=mode,
            executed_mode=mode,
            degraded=False,
            degraded_reason=None,
            lexical_rows=lexical_rows,
            top_k=top_k,
            query_text=query_text,
            corpus=corpus,
            row_marker=row_marker,
            effective_query_text=effective_query_text,
            query_rewrite=rewrite,
            semantic_retry_events=semantic_retry_events,
            score_definitions=score_definitions,
            workload=workload,
            started=started,
            timing=timing,
            timing_payload=_timing_payload,
            row_projection_policy=resolved_row_projection_policy,
            apply_corpus_row_policy=lambda rows, query_text, corpus: _apply_corpus_row_policy(
                rows,
                query_text=query_text,
                corpus=corpus,
            ),
            build_row_projection=_build_row_projection,
            apply_abstain_policy=lambda projection, policy: _apply_abstain_policy(
                projection,
                policy=policy,
            ),
            lexical_weight=LEXICAL_WEIGHT,
            semantic_weight=SEMANTIC_WEIGHT,
            rerank_weight=RERANK_WEIGHT,
        )

    preflight_started = time.perf_counter()
    preflight = check_semantic_backend(semantic_config)
    timing["preflight_ms"] += (time.perf_counter() - preflight_started) * 1000.0
    if not bool(preflight.get("ok", False)):
        error_code = "SEMANTIC_BACKEND_UNAVAILABLE"
        if mode == "hybrid":
            error_code = "HYBRID_BACKEND_UNAVAILABLE"

        if not allow_degraded:
            detail = preflight.get("checks", [])
            raise ModeExecutionError(
                code=error_code,
                message=f"Semantic backend preflight failed: {detail}",
            )

        lexical_started = time.perf_counter()
        lexical_rows = _run_lexical()
        timing["lexical_ms"] += (time.perf_counter() - lexical_started) * 1000.0
        return finalize_lexical_like_result(
            requested_mode=mode,
            executed_mode="lexical",
            degraded=True,
            degraded_reason=error_code,
            lexical_rows=lexical_rows,
            top_k=top_k,
            query_text=query_text,
            corpus=corpus,
            row_marker=row_marker,
            effective_query_text=effective_query_text,
            query_rewrite=rewrite,
            semantic_retry_events=semantic_retry_events,
            score_definitions=score_definitions,
            workload=workload,
            started=started,
            timing=timing,
            timing_payload=_timing_payload,
            row_projection_policy=resolved_row_projection_policy,
            apply_corpus_row_policy=lambda rows, query_text, corpus: _apply_corpus_row_policy(
                rows,
                query_text=query_text,
                corpus=corpus,
            ),
            build_row_projection=_build_row_projection,
            apply_abstain_policy=lambda projection, policy: _apply_abstain_policy(
                projection,
                policy=policy,
            ),
            lexical_weight=LEXICAL_WEIGHT,
            semantic_weight=SEMANTIC_WEIGHT,
            rerank_weight=RERANK_WEIGHT,
            preflight=preflight,
        )

    ensure_embedding_cache_table(db_path, retrieval_contract)

    try:
        semantic_rows = _semantic_candidates(
            db_path=db_path,
            retrieval_contract=retrieval_contract,
            config=semantic_config,
            retries=semantic_retries,
            query_text=effective_query_text,
            corpus_rows=corpus_rows,
            top_k=top_k,
            candidate_limit=candidate_limit,
            persist_cache=persist_semantic_cache,
            allow_online_corpus_embedding=allow_online_corpus_embedding,
            retry_events=semantic_retry_events,
            timing=timing,
            workload=workload,
        )
    except ModeExecutionError as exc:
        mapped_code = str(exc.code)
        if mode == "hybrid":
            if mapped_code == "SEMANTIC_BACKEND_UNAVAILABLE":
                mapped_code = "HYBRID_BACKEND_UNAVAILABLE"
            elif mapped_code == "SEMANTIC_INDEX_INCOMPLETE":
                mapped_code = "HYBRID_INDEX_INCOMPLETE"

        if not allow_degraded:
            raise ModeExecutionError(code=mapped_code, message=str(exc)) from exc

        lexical_started = time.perf_counter()
        lexical_rows = _run_lexical()
        timing["lexical_ms"] += (time.perf_counter() - lexical_started) * 1000.0
        return finalize_lexical_like_result(
            requested_mode=mode,
            executed_mode="lexical",
            degraded=True,
            degraded_reason=mapped_code,
            lexical_rows=lexical_rows,
            top_k=top_k,
            query_text=query_text,
            corpus=corpus,
            row_marker=row_marker,
            effective_query_text=effective_query_text,
            query_rewrite=rewrite,
            semantic_retry_events=semantic_retry_events,
            score_definitions=score_definitions,
            workload=workload,
            started=started,
            timing=timing,
            timing_payload=_timing_payload,
            row_projection_policy=resolved_row_projection_policy,
            apply_corpus_row_policy=lambda rows, query_text, corpus: _apply_corpus_row_policy(
                rows,
                query_text=query_text,
                corpus=corpus,
            ),
            build_row_projection=_build_row_projection,
            apply_abstain_policy=lambda projection, policy: _apply_abstain_policy(
                projection,
                policy=policy,
            ),
            lexical_weight=LEXICAL_WEIGHT,
            semantic_weight=SEMANTIC_WEIGHT,
            rerank_weight=RERANK_WEIGHT,
            preflight=preflight,
        )

    _annotate_rows_with_row_markers(semantic_rows, row_profiles)
    semantic_rows = _filter_rows_by_row_marker(semantic_rows, row_marker)
    workload["semantic_pool_size"] = int(len(semantic_rows))

    if mode == "semantic":
        return finalize_semantic_mode(
            mode=mode,
            semantic_rows=semantic_rows,
            top_k=top_k,
            query_text=query_text,
            corpus=corpus,
            row_marker=row_marker,
            effective_query_text=effective_query_text,
            query_rewrite=rewrite,
            semantic_retry_events=semantic_retry_events,
            score_definitions=score_definitions,
            workload=workload,
            started=started,
            timing=timing,
            timing_payload=_timing_payload,
            preflight=preflight,
            apply_corpus_row_policy=lambda rows, query_text, corpus: _apply_corpus_row_policy(
                rows,
                query_text=query_text,
                corpus=corpus,
            ),
            build_row_projection=_build_row_projection,
            apply_abstain_policy=lambda projection, policy: _apply_abstain_policy(
                projection,
                policy=policy,
            ),
            row_projection_policy=resolved_row_projection_policy,
            row_identity=_row_identity,
            lexical_weight=LEXICAL_WEIGHT,
            semantic_weight=SEMANTIC_WEIGHT,
            rerank_weight=RERANK_WEIGHT,
        )

    lexical_started = time.perf_counter()
    lexical_rows = _run_lexical()
    timing["lexical_ms"] += (time.perf_counter() - lexical_started) * 1000.0

    def _rerank_with_retries(query: str, documents: list[str]) -> list[float]:
        return _with_semantic_retries(
            "hybrid reranker scoring",
            semantic_retries,
            lambda: semantic_rerank(query, documents),
            telemetry=semantic_retry_events,
        )

    def semantic_rerank(query: str, documents: list[str]) -> list[float]:
        from semantic_backend_client import rerank_texts

        return rerank_texts(
            config=semantic_config,
            query_text=query,
            documents=documents,
        )

    return finalize_hybrid_mode(
        mode=mode,
        lexical_rows=lexical_rows,
        semantic_rows=semantic_rows,
        candidate_limit=candidate_limit,
        normalized_candidate_policy=normalized_candidate_policy,
        normalized_fusion_method=normalized_fusion_method,
        effective_query_text=effective_query_text,
        top_k=top_k,
        resolved_hybrid_rerank_pool_size=resolved_hybrid_rerank_pool_size,
        resolved_hybrid_lexical_min=resolved_hybrid_lexical_min,
        resolved_hybrid_semantic_min=resolved_hybrid_semantic_min,
        resolved_lexical_floor_count=resolved_lexical_floor_count,
        resolved_lexical_floor_share=resolved_lexical_floor_share,
        resolved_rrf_k=resolved_rrf_k,
        resolved_rrf_window=resolved_rrf_window,
        lexical_weight=LEXICAL_WEIGHT,
        semantic_weight=SEMANTIC_WEIGHT,
        rerank_weight=RERANK_WEIGHT,
        weighted_v2_lexical_weight=WEIGHTED_V2_LEXICAL_WEIGHT,
        weighted_v2_semantic_weight=WEIGHTED_V2_SEMANTIC_WEIGHT,
        weighted_v2_rerank_weight=WEIGHTED_V2_RERANK_WEIGHT,
        row_identity=_row_identity,
        rerank_documents=_rerank_with_retries,
        timing=timing,
        workload=workload,
        query_text=query_text,
        corpus=corpus,
        row_marker=row_marker,
        query_rewrite=rewrite,
        semantic_retry_events=semantic_retry_events,
        score_definitions=score_definitions,
        fusion_params=fusion_params,
        started=started,
        timing_payload=_timing_payload,
        preflight=preflight,
        apply_corpus_row_policy=lambda rows, query_text, corpus: _apply_corpus_row_policy(
            rows,
            query_text=query_text,
            corpus=corpus,
        ),
        build_row_projection=_build_row_projection,
        apply_abstain_policy=lambda projection, policy: _apply_abstain_policy(
            projection,
            policy=policy,
        ),
        row_projection_policy=resolved_row_projection_policy,
    )


def _without_score_breakdown(result: dict[str, Any]) -> dict[str, Any]:
    return core_without_score_breakdown(result, score_fields=SCORE_FIELDS)


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[3]
    corpus = str(args.corpus).strip().lower()

    retrieval_profile_path = str(args.retrieval_profile_path).strip()
    if retrieval_profile_path:
        profile_path = Path(retrieval_profile_path)
        if not profile_path.is_absolute():
            profile_path = (root / profile_path).resolve()
        profile = load_retrieval_profile(profile_path)
        corpus = enforce_profile_corpus(corpus, profile)
        apply_profile_defaults(args, profile)

    runtime_paths = resolve_corpus_runtime_paths(
        root=root,
        corpus=corpus,
        db_path=str(args.db_path),
        contract_path=str(args.contract_path),
        query_log_root=str(args.query_log_root),
        rewrite_rules_path=str(args.rewrite_rules_path),
    )
    db_path = runtime_paths.db_path
    contract_path = runtime_paths.contract_path
    query_log_root = runtime_paths.query_log_root
    rewrite_path = runtime_paths.rewrite_rules_path
    row_projection_policy = resolve_row_projection_policy(root=root, corpus=corpus)

    rewrite_mode = str(args.rewrite_mode)
    if rewrite_mode == "auto" and not rewrite_path.exists():
        rewrite_mode = "off"

    query_text = str(getattr(args, "query_text", "")).strip()
    allow_degraded = bool(getattr(args, "allow_degraded", False))

    semantic_config: SemanticBackendConfig | None = None

    try:
        if args.mode == "contract":
            if not args.query_id:
                raise GuardrailError("--query-id is required when --mode contract")
            params = _parse_params(args.params_json)
            result = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id=args.query_id,
                params=params,
                row_limit=args.row_limit,
                query_log_root=query_log_root,
            )
        else:
            if not query_text:
                raise GuardrailError("--query-text is required for lexical/semantic/hybrid modes")

            semantic_config = _build_semantic_config(args)
            result = execute_retrieval_query(
                mode=args.mode,
                db_path=db_path,
                contract_path=contract_path,
                query_log_root=query_log_root,
                query_text=query_text,
                row_marker=str(args.row_marker),
                top_k=int(args.top_k),
                candidate_limit=int(args.candidate_limit),
                allow_degraded=allow_degraded,
                semantic_config=semantic_config,
                semantic_retries=int(args.semantic_retries),
                persist_semantic_cache=bool(args.persist_semantic_cache),
                allow_online_corpus_embedding=bool(args.allow_online_corpus_embedding),
                rewrite_mode=rewrite_mode,
                rewrite_rules_path=rewrite_path,
                hybrid_fusion_method=str(args.hybrid_fusion_method),
                hybrid_rrf_k=int(args.hybrid_rrf_k),
                hybrid_rrf_window=int(args.hybrid_rrf_window),
                hybrid_lexical_floor_count=int(args.hybrid_lexical_floor_count),
                hybrid_lexical_floor_share=float(args.hybrid_lexical_floor_share),
                hybrid_candidate_policy=str(args.hybrid_candidate_policy),
                hybrid_rerank_pool_size=int(args.hybrid_rerank_pool_size),
                hybrid_lexical_min=int(args.hybrid_lexical_min),
                hybrid_semantic_min=int(args.hybrid_semantic_min),
                corpus=corpus,
                row_projection_policy=row_projection_policy,
            )
            if not bool(args.include_score_breakdown):
                result = _without_score_breakdown(result)

        review_artifact_path = resolve_review_artifact_path(
            root=root,
            mode=str(args.mode),
            query_text=query_text,
            prompt_id=str(args.prompt_id),
            save_response_path=str(args.save_response_path),
            save_response_dir=str(args.save_response_dir),
            default_query_review_dir=DEFAULT_QUERY_REVIEW_DIR,
        )
        if review_artifact_path is not None:
            review_payload = build_review_artifact_payload(
                mode=str(args.mode),
                query_text=query_text,
                row_marker=str(args.row_marker),
                prompt_id=str(args.prompt_id),
                top_k=int(args.top_k),
                candidate_limit=int(args.candidate_limit),
                include_score_breakdown=bool(args.include_score_breakdown),
                allow_degraded=allow_degraded,
                db_path=db_path,
                contract_path=contract_path,
                query_log_root=query_log_root,
                semantic_config=semantic_config,
                semantic_retries=int(args.semantic_retries),
                persist_semantic_cache=bool(args.persist_semantic_cache),
                allow_online_corpus_embedding=bool(args.allow_online_corpus_embedding),
                response=result,
                review_artifact_schema_version=REVIEW_ARTIFACT_SCHEMA_VERSION,
            )
            persist_review_artifact(review_artifact_path, review_payload)
            print(
                f"[query-rust-reference][artifact] wrote {review_artifact_path}",
                file=sys.stderr,
            )
    except ModeExecutionError as exc:
        print(f"[query-rust-reference][error][{exc.code}] {exc}")
        return EXIT_RUNTIME_FAIL
    except (json.JSONDecodeError, GuardrailError, OSError, sqlite3.Error) as exc:
        print(f"[query-rust-reference][error] {exc}")
        return EXIT_RUNTIME_FAIL

    print(json.dumps(result, indent=2, sort_keys=True))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
