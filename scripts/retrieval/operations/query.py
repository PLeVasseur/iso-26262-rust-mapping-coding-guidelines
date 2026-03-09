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
from retrieval.query.operation_main import run_query_main
from retrieval.query.policy_resolution import RowProjectionPolicy
from retrieval.query.policy_resolution import (
    resolve_row_projection_policy,
    row_projection_policy_from_globals as _row_projection_policy_from_globals,
)
from retrieval.query.result_payload import build_retrieval_result
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
from sqlite_query_guardrails import GuardrailError

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


def _normalize_allowed_scope_ids(
    allowed_scope_ids: list[str] | tuple[str, ...] | set[str] | None,
) -> tuple[str, int, list[str]]:
    if allowed_scope_ids is None:
        return "global", 0, []
    raw_values = [str(value) for value in allowed_scope_ids]
    requested_count = len(raw_values)
    normalized = sorted({value.strip() for value in raw_values if value.strip()})
    if normalized:
        return "restricted_subset", requested_count, normalized
    return "restricted_empty", requested_count, []


def _filter_rows_to_allowed_ids(
    rows: list[dict[str, Any]],
    *,
    allowed_scope_ids: set[str],
    scope_id_field: str,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    return [row for row in rows if str(row.get(scope_id_field, "")).strip() in allowed_scope_ids]


def _resolve_scope_id_field(rows: list[dict[str, Any]]) -> str:
    if any(str(row.get("paragraph_id", "")).strip() for row in rows):
        return "paragraph_id"
    if any(str(row.get("chunk_uid", "")).strip() for row in rows):
        return "chunk_uid"
    return "statement_id"


def _default_scope_id_field(retrieval_contract: RetrievalContractProfile, *, corpus: str) -> str:
    if str(corpus).strip() == "fls_spec":
        return "paragraph_id"
    if retrieval_contract.corpus_query_id == "chunk_corpus_v1_all":
        return retrieval_contract.lexical_id_column
    return "statement_id"


def _build_scope_payload(
    *,
    scope_state: str,
    requested_count: int,
    scope_id_field: str,
    allowed_scope_ids: list[str],
    scoped_corpus_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    matched_ids = {
        str(row.get(scope_id_field, "")).strip()
        for row in scoped_corpus_rows
        if str(row.get(scope_id_field, "")).strip()
    }
    payload = {
        "state": scope_state,
        "scope_id_field": scope_id_field,
        "requested_count": int(requested_count),
        "normalized_count": int(len(allowed_scope_ids)),
        "matched_count": int(len(matched_ids)),
        "unmatched_count": int(max(0, len(allowed_scope_ids) - len(matched_ids))),
        "allowed_scope_ids": list(allowed_scope_ids),
    }
    return payload


def _build_empty_scope_result(
    *,
    mode: str,
    query_text: str,
    effective_query_text: str,
    query_rewrite: dict[str, Any],
    row_marker: str,
    score_definitions: dict[str, str],
    workload: dict[str, int],
    timing: dict[str, float],
    timing_payload: Any,
    started: float,
    scope: dict[str, Any],
    preflight: dict[str, Any] | None = None,
    fusion_params: dict[str, Any] | None = None,
    normalized_fusion_method: str | None = None,
) -> dict[str, Any]:
    duration_ms = (time.perf_counter() - started) * 1000.0
    extras: dict[str, Any] | None = None
    if mode == "hybrid":
        extras = {
            "fusion_method": normalized_fusion_method,
            "fusion_params": fusion_params or {},
            "fusion_debug": {},
        }
    return build_retrieval_result(
        requested_mode=mode,
        executed_mode=mode,
        degraded=False,
        semantic_retry_events=[],
        score_definitions=score_definitions,
        workload=workload,
        query_text=query_text,
        effective_query_text=effective_query_text,
        query_rewrite=query_rewrite,
        row_marker=row_marker,
        rows=[],
        duration_ms=duration_ms,
        timing=timing_payload(duration_ms),
        row_projection=[],
        row_projection_all=[],
        abstain={"should_abstain": False, "reason": "restricted_empty_scope", "threshold": None},
        preflight=preflight,
        extras=extras,
        scope=scope,
    )


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
    allowed_scope_ids: set[str] | None,
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
        allowed_scope_ids=allowed_scope_ids,
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
    allowed_statement_ids: list[str] | tuple[str, ...] | set[str] | None = None,
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
    scope_state, requested_scope_count, normalized_allowed_scope_ids = _normalize_allowed_scope_ids(
        allowed_statement_ids
    )
    allowed_scope_id_set = set(normalized_allowed_scope_ids)

    retrieval_contract = _resolve_retrieval_contract_profile(contract_path)

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

    if scope_state == "restricted_empty":
        return _build_empty_scope_result(
            mode=mode,
            query_text=query_text,
            effective_query_text=effective_query_text,
            query_rewrite=rewrite,
            row_marker=row_marker,
            score_definitions=score_definitions,
            workload=workload,
            timing=timing,
            timing_payload=_timing_payload,
            started=started,
            scope=_build_scope_payload(
                scope_state=scope_state,
                requested_count=requested_scope_count,
                scope_id_field=_default_scope_id_field(retrieval_contract, corpus=corpus),
                allowed_scope_ids=normalized_allowed_scope_ids,
                scoped_corpus_rows=[],
            ),
            fusion_params=fusion_params,
            normalized_fusion_method=normalized_fusion_method,
        )

    corpus_rows_all = _load_statement_corpus(
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
    corpus_rows = corpus_rows_all
    if scope_state == "restricted_subset":
        scope_id_field = _resolve_scope_id_field(corpus_rows_all)
        corpus_rows = _filter_rows_to_allowed_ids(
            corpus_rows_all,
            allowed_scope_ids=allowed_scope_id_set,
            scope_id_field=scope_id_field,
        )
    else:
        scope_id_field = _resolve_scope_id_field(corpus_rows)
    scope = _build_scope_payload(
        scope_state=scope_state,
        requested_count=requested_scope_count,
        scope_id_field=scope_id_field,
        allowed_scope_ids=normalized_allowed_scope_ids,
        scoped_corpus_rows=corpus_rows,
    )

    if scope_state == "restricted_subset" and not corpus_rows:
        return _build_empty_scope_result(
            mode=mode,
            query_text=query_text,
            effective_query_text=effective_query_text,
            query_rewrite=rewrite,
            row_marker=row_marker,
            score_definitions=score_definitions,
            workload=workload,
            timing=timing,
            timing_payload=_timing_payload,
            started=started,
            scope=scope,
            fusion_params=fusion_params,
            normalized_fusion_method=normalized_fusion_method,
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
            allowed_scope_ids=(
                allowed_scope_id_set if scope_state == "restricted_subset" else None
            ),
        )

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
            scope=scope,
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
            scope=scope,
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
            scope=scope,
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
            scope=scope,
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
        scope=scope,
    )


def _without_score_breakdown(result: dict[str, Any]) -> dict[str, Any]:
    return core_without_score_breakdown(result, score_fields=SCORE_FIELDS)


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[3]
    return run_query_main(
        args=args,
        root=root,
        parse_params=_parse_params,
        build_semantic_config=_build_semantic_config,
        execute_retrieval_query=execute_retrieval_query,
        without_score_breakdown=_without_score_breakdown,
        default_query_review_dir=DEFAULT_QUERY_REVIEW_DIR,
        review_artifact_schema_version=REVIEW_ARTIFACT_SCHEMA_VERSION,
    )


if __name__ == "__main__":
    sys.exit(main())
