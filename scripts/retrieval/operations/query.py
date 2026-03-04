#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from retrieval.core.candidate_policy import (
    apply_hybrid_candidate_policy_v2_rerank as core_apply_hybrid_candidate_policy_v2_rerank,
)
from retrieval.core.candidate_policy import (
    apply_hybrid_lexical_floor_rerank as core_apply_hybrid_lexical_floor_rerank,
)
from retrieval.core.engine import build_runtime_config
from retrieval.core.fusion import (
    apply_component_scores as core_apply_component_scores,
)
from retrieval.core.fusion import apply_rrf_hybrid_scores as core_apply_rrf_hybrid_scores
from retrieval.core.policy_loader import load_eval_policy
from retrieval.core.profile import (
    DEFAULT_HYBRID_RRF_K,
    HYBRID_CANDIDATE_POLICIES,
    HYBRID_CANDIDATE_POLICY_LEGACY,
    HYBRID_CANDIDATE_POLICY_V2,
    HYBRID_FUSION_METHODS,
    HYBRID_FUSION_RRF_V1,
    HYBRID_FUSION_WEIGHTED_V1,
    HYBRID_FUSION_WEIGHTED_V2,
)
from retrieval.core.profile_loader import (
    apply_profile_defaults,
    enforce_profile_corpus,
    load_retrieval_profile,
)
from retrieval.core.rewrite import rewrite_query_text as core_rewrite_query_text
from retrieval.core.telemetry import (
    init_retrieval_timing,
    init_retrieval_workload,
)
from retrieval.core.telemetry import (
    timing_payload as core_timing_payload,
)
from retrieval.corpora.registry import get_corpus_adapter, list_supported_corpora
from retrieval.corpora.runtime_paths import resolve_corpus_runtime_paths
from retrieval.query.backend_retry import with_semantic_retries as _with_semantic_retries
from retrieval.query.embedding_cache import (
    ensure_embedding_cache_table,
)
from retrieval.query.errors import ModeExecutionError
from retrieval.query.lexical_pipeline import load_statement_corpus as core_load_statement_corpus
from retrieval.query.lexical_pipeline import (
    load_table1_row_requirements as core_load_table1_row_requirements,
)
from retrieval.query.lexical_pipeline import run_lexical_query as core_run_lexical_query
from retrieval.query.semantic_math import min_max_normalize
from retrieval.query.row_markers import (
    annotate_rows_with_row_markers as _annotate_rows_with_row_markers,
)
from retrieval.query.row_markers import (
    filter_rows_by_row_marker as _filter_rows_by_row_marker,
)
from retrieval.query.rewrite_rules import load_rewrite_rules as _load_rewrite_rules
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
    rerank_texts,
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

ROW_PROJECTION_THRESHOLDS = {
    "1a": 0.015,
    "1b": 0.015,
    "1c": 0.015,
    "1d": 0.015,
    "1e": 0.015,
    "1f": 0.015,
    "1g": 0.015,
    "1h": 0.015,
    "1i": 0.020,
}
ROW_PROJECTION_TOP_SCORE_FLOOR = 0.0
ROW_PROJECTION_MIN_EVIDENCE_HITS = 1
ROW_PROJECTION_MARGIN = 0.005


@dataclass(frozen=True)
class RowProjectionPolicy:
    thresholds: dict[str, float]
    top_score_floor: float
    min_evidence_hits: int
    margin: float


def _row_projection_policy_from_globals() -> RowProjectionPolicy:
    return RowProjectionPolicy(
        thresholds={
            str(marker).strip().lower(): float(value)
            for marker, value in ROW_PROJECTION_THRESHOLDS.items()
            if str(marker).strip()
        },
        top_score_floor=float(ROW_PROJECTION_TOP_SCORE_FLOOR),
        min_evidence_hits=max(1, int(ROW_PROJECTION_MIN_EVIDENCE_HITS)),
        margin=max(0.0, float(ROW_PROJECTION_MARGIN)),
    )


def resolve_row_projection_policy(*, root: Path, corpus: str) -> RowProjectionPolicy:
    policy = _row_projection_policy_from_globals()
    policy_path = (root / str(get_corpus_adapter(corpus).config.default_eval_policy_path)).resolve()
    if policy_path.exists():
        loaded = load_eval_policy(policy_path)
        projection = loaded.get("projection_thresholds") or {}
        thresholds = dict(policy.thresholds)
        if isinstance(projection, dict):
            resolved_thresholds = {
                str(marker).strip().lower(): float(value)
                for marker, value in projection.items()
                if str(marker).strip()
            }
            default_threshold = float(
                resolved_thresholds.get(
                    "default",
                    min(thresholds.values()) if thresholds else 0.015,
                )
            )
            row_markers = tuple(f"1{chr(ord('a') + idx)}" for idx in range(9))
            thresholds = {
                marker: float(resolved_thresholds.get(marker, default_threshold))
                for marker in row_markers
            }

        abstain_policy = loaded.get("abstain_policy") or {}
        top_floor = policy.top_score_floor
        min_hits = policy.min_evidence_hits
        margin = policy.margin
        if isinstance(abstain_policy, dict):
            configured_top_floor = abstain_policy.get("top_score_floor")
            configured_min_hits = abstain_policy.get("min_evidence_hits")
            configured_margin = abstain_policy.get("margin")
            if configured_top_floor is not None:
                top_floor = max(0.0, float(configured_top_floor))
            if configured_min_hits is not None:
                min_hits = max(1, int(configured_min_hits))
            if configured_margin is not None:
                margin = max(0.0, float(configured_margin))

        policy = RowProjectionPolicy(
            thresholds=thresholds,
            top_score_floor=float(top_floor),
            min_evidence_hits=max(1, int(min_hits)),
            margin=max(0.0, float(margin)),
        )

    env_top_floor = str(os.getenv("SQLKB_ROW_PROJECTION_TOP_SCORE_FLOOR", "")).strip()
    env_min_hits = str(os.getenv("SQLKB_ROW_PROJECTION_MIN_EVIDENCE_HITS", "")).strip()
    env_margin = str(os.getenv("SQLKB_ROW_PROJECTION_MARGIN", "")).strip()
    top_floor = policy.top_score_floor
    min_hits = policy.min_evidence_hits
    margin = policy.margin
    if env_top_floor:
        top_floor = max(0.0, float(env_top_floor))
    if env_min_hits:
        min_hits = max(1, int(env_min_hits))
    if env_margin:
        margin = max(0.0, float(env_margin))

    return RowProjectionPolicy(
        thresholds=dict(policy.thresholds),
        top_score_floor=float(top_floor),
        min_evidence_hits=max(1, int(min_hits)),
        margin=max(0.0, float(margin)),
    )


CHUNK_REQUIRED_QUERY_IDS = {
    "chunk_corpus_v1_all",
    "lexical_chunk_search_v1",
    "table1_row_requirements_v2",
}
LEGACY_REQUIRED_QUERY_IDS = {
    "statement_corpus_v3_all",
    "lexical_statement_search_v2",
    "table1_row_requirements_v1",
}


@dataclass(frozen=True)
class RetrievalContractProfile:
    corpus_query_id: str
    corpus_cursor_param: str
    lexical_query_id: str
    lexical_id_column: str
    row_requirements_query_id: str
    embedding_table: str
    embedding_id_column: str
    embed_version: str


def _load_contract_query_ids(contract_path: Path) -> set[str]:
    with contract_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise GuardrailError("Contract payload must be a mapping")

    raw_queries = payload.get("queries") or {}
    if not isinstance(raw_queries, dict) or not raw_queries:
        raise GuardrailError("Contract must define a non-empty queries mapping")
    return {str(query_id).strip() for query_id in raw_queries.keys() if str(query_id).strip()}


def _resolve_retrieval_contract_profile(contract_path: Path) -> RetrievalContractProfile:
    query_ids = _load_contract_query_ids(contract_path)
    chunk_present = sorted(CHUNK_REQUIRED_QUERY_IDS.intersection(query_ids))
    if chunk_present:
        missing_chunk = sorted(CHUNK_REQUIRED_QUERY_IDS.difference(query_ids))
        if missing_chunk:
            raise GuardrailError(
                "Chunk retrieval contract is incomplete; missing query ids: "
                + ", ".join(missing_chunk)
            )
        return RetrievalContractProfile(
            corpus_query_id="chunk_corpus_v1_all",
            corpus_cursor_param="chunk_uid_after",
            lexical_query_id="lexical_chunk_search_v1",
            lexical_id_column="chunk_uid",
            row_requirements_query_id="table1_row_requirements_v2",
            embedding_table="chunk_embeddings",
            embedding_id_column="chunk_uid",
            embed_version="chunk-v1",
        )

    missing_legacy = sorted(LEGACY_REQUIRED_QUERY_IDS.difference(query_ids))
    if missing_legacy:
        raise GuardrailError(
            "Contract missing retrieval query ids. Expected either chunk ids "
            f"{sorted(CHUNK_REQUIRED_QUERY_IDS)} or legacy ids "
            f"{sorted(LEGACY_REQUIRED_QUERY_IDS)}; missing legacy ids: {missing_legacy}"
        )
    return RetrievalContractProfile(
        corpus_query_id="statement_corpus_v3_all",
        corpus_cursor_param="statement_id_after",
        lexical_query_id="lexical_statement_search_v2",
        lexical_id_column="statement_id",
        row_requirements_query_id="table1_row_requirements_v1",
        embedding_table="statement_embeddings",
        embedding_id_column="statement_id",
        embed_version="statement-v1",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only query wrapper for rust_reference.sqlite"
    )
    parser.add_argument(
        "--corpus",
        choices=list_supported_corpora(),
        default="rust_reference",
        help="Corpus adapter used to resolve default DB/contract paths",
    )
    parser.add_argument(
        "--mode",
        choices=("contract", "lexical", "semantic", "hybrid"),
        default="contract",
        help="Query mode (contract passthrough or retrieval mode)",
    )
    parser.add_argument("--query-id", default=None, help="Contract query id to execute")
    parser.add_argument(
        "--params-json",
        default="{}",
        help="JSON object of named params passed to the contract query",
    )
    parser.add_argument(
        "--query-text",
        default="",
        help="Natural-language query text for lexical/semantic/hybrid retrieval",
    )
    parser.add_argument(
        "--retrieval-profile-path",
        default="",
        help="Optional retrieval profile YAML for model/fusion defaults",
    )
    parser.add_argument(
        "--row-marker",
        default="",
        help="Optional Table 1 row marker filter (1a..1i)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help="Maximum number of retrieval rows returned",
    )
    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=DEFAULT_CANDIDATE_LIMIT,
        help="Maximum candidate statement rows considered during retrieval",
    )
    parser.add_argument(
        "--hybrid-fusion-method",
        choices=HYBRID_FUSION_METHODS,
        default=HYBRID_FUSION_WEIGHTED_V1,
        help="Hybrid fusion method",
    )
    parser.add_argument(
        "--hybrid-rrf-k",
        type=int,
        default=DEFAULT_HYBRID_RRF_K,
        help="RRF rank constant k for --hybrid-fusion-method rrf-v1",
    )
    parser.add_argument(
        "--hybrid-rrf-window",
        type=int,
        default=0,
        help="Optional rank window for RRF (0 means auto max(top_k*8,64))",
    )
    parser.add_argument(
        "--hybrid-candidate-policy",
        choices=HYBRID_CANDIDATE_POLICIES,
        default=HYBRID_CANDIDATE_POLICY_LEGACY,
        help="Hybrid candidate assembly policy before fusion",
    )
    parser.add_argument(
        "--hybrid-rerank-pool-size",
        type=int,
        default=0,
        help="Hybrid rerank pool target size (0 means auto max(top_k*8,64))",
    )
    parser.add_argument(
        "--hybrid-lexical-min",
        type=int,
        default=0,
        help="Minimum lexical candidates included in hybrid rerank pool when policy=v2",
    )
    parser.add_argument(
        "--hybrid-semantic-min",
        type=int,
        default=0,
        help="Minimum semantic candidates included in hybrid rerank pool when policy=v2",
    )
    parser.add_argument(
        "--hybrid-lexical-floor-count",
        type=int,
        default=0,
        help="Minimum lexical candidates to include in hybrid reranker pool",
    )
    parser.add_argument(
        "--hybrid-lexical-floor-share",
        type=float,
        default=0.0,
        help="Minimum lexical share of hybrid reranker window [0,1]",
    )
    parser.add_argument(
        "--include-score-breakdown",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include score component fields in retrieval output",
    )
    parser.add_argument(
        "--prompt-id",
        default="",
        help="Optional prompt identifier for saved review artifacts",
    )
    parser.add_argument(
        "--save-response-path",
        default="",
        help="Optional JSON path to persist full query response review artifact",
    )
    parser.add_argument(
        "--save-response-dir",
        default="",
        help=(
            "Optional directory for persisted review artifact JSON using "
            "<timestamp>__<prompt_id>__<mode>.json naming"
        ),
    )
    parser.add_argument(
        "--allow-degraded",
        action="store_true",
        help="Allow lexical degraded fallback if semantic backend is unavailable",
    )
    parser.add_argument(
        "--semantic-base-url",
        default="http://127.0.0.1:8080",
        help="Fallback semantic backend base URL",
    )
    parser.add_argument(
        "--semantic-embed-base-url",
        default="http://127.0.0.1:8080",
        help="Optional embedding backend base URL override",
    )
    parser.add_argument(
        "--semantic-rerank-base-url",
        default="http://127.0.0.1:8081",
        help="Optional reranker backend base URL override",
    )
    parser.add_argument(
        "--embed-model-id",
        default="Qwen/Qwen3-Embedding-4B",
        help="Embedding model identifier",
    )
    parser.add_argument(
        "--reranker-model-id",
        default="BAAI/bge-reranker-v2-m3",
        help="Reranker model identifier",
    )
    parser.add_argument(
        "--semantic-timeout-sec",
        type=float,
        default=60.0,
        help="Timeout for semantic backend HTTP requests",
    )
    parser.add_argument(
        "--semantic-retries",
        type=int,
        default=0,
        help="Retry count for transient semantic backend failures",
    )
    parser.add_argument(
        "--persist-semantic-cache",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Persist per-statement embeddings in sqlite cache table",
    )
    parser.add_argument(
        "--allow-online-corpus-embedding",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Allow semantic query path to embed missing corpus rows on demand "
            "(disabled by default; prefer materialize-first)"
        ),
    )
    parser.add_argument(
        "--db-path",
        default="",
        help="Path to corpus sqlite database (defaults from --corpus)",
    )
    parser.add_argument(
        "--contract-path",
        default="",
        help="Path to corpus query contract YAML (defaults from --corpus)",
    )
    parser.add_argument(
        "--query-log-root",
        default="",
        help="Directory used for query audit logs (defaults by corpus)",
    )
    parser.add_argument(
        "--rewrite-mode",
        choices=("auto", "off"),
        default="auto",
        help="Deterministic query rewrite mode",
    )
    parser.add_argument(
        "--rewrite-rules-path",
        default="",
        help="Path to deterministic query rewrite rules YAML (defaults by corpus)",
    )
    parser.add_argument(
        "--row-limit",
        type=int,
        default=None,
        help="Optional override for row limit (guardrailed)",
    )
    return parser.parse_args()


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
    row_scores: dict[str, float] = {}
    row_evidence_count: dict[str, int] = {}
    row_evidence_trace: dict[str, list[dict[str, Any]]] = {}

    for rank, row in enumerate(rows, start=1):
        relevance = float(
            row.get(
                "relevance_score",
                row.get("lexical_score", 0.0),
            )
        )
        rank_weight = 1.0 / float(rank)
        marker_scores = row.get("row_marker_scores", [])
        if not isinstance(marker_scores, list):
            marker_scores = []

        for marker_score in marker_scores:
            if not isinstance(marker_score, dict):
                continue
            key = str(marker_score.get("row_marker", "")).strip().lower()
            if not key:
                continue
            profile_score = float(marker_score.get("score", 0.0))
            if profile_score <= 0.0:
                continue
            contribution = relevance * rank_weight * profile_score
            row_scores[key] = row_scores.get(key, 0.0) + contribution
            row_evidence_count[key] = row_evidence_count.get(key, 0) + 1
            row_evidence_trace.setdefault(key, []).append(
                {
                    "statement_id": str(row.get("statement_id", "")),
                    "source_anchor": str(row.get("source_anchor", "")),
                    "contribution": float(contribution),
                }
            )

    projection: list[dict[str, Any]] = []
    for marker, score in row_scores.items():
        trace = row_evidence_trace.get(marker, [])
        trace.sort(
            key=lambda row: (
                -float(row.get("contribution", 0.0)),
                str(row.get("statement_id", "")),
            )
        )
        rounded_trace = [
            {
                "statement_id": str(item.get("statement_id", "")),
                "source_anchor": str(item.get("source_anchor", "")),
                "contribution": round(float(item.get("contribution", 0.0)), 6),
            }
            for item in trace[:10]
        ]

        top = rounded_trace[0] if rounded_trace else {}
        projection.append(
            {
                "row_marker": marker,
                "score": round(score, 6),
                "evidence_hits": int(row_evidence_count.get(marker, 0)),
                "top_statement_id": str(top.get("statement_id", "")),
                "top_source_anchor": str(top.get("source_anchor", "")),
                "evidence_trace": rounded_trace,
            }
        )

    projection.sort(key=lambda row: (-float(row["score"]), str(row["row_marker"])))
    return projection


def _apply_abstain_policy(
    projection: list[dict[str, Any]],
    *,
    policy: RowProjectionPolicy,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    thresholds = dict(policy.thresholds)
    top_score_floor = float(policy.top_score_floor)
    min_evidence_hits = max(1, int(policy.min_evidence_hits))
    margin_threshold = max(0.0, float(policy.margin))
    if not projection:
        return [], {
            "active": True,
            "reason_code": "NO_ROW_SIGNAL",
            "detail": "No row marker evidence was generated from retrieved chunks",
            "thresholds": thresholds,
        }

    top = projection[0]
    top_marker = str(top.get("row_marker", "")).strip().lower()
    top_score = float(top.get("score", 0.0))
    top_hits = int(top.get("evidence_hits", 0))
    threshold = float(thresholds.get(top_marker, 0.015))
    effective_threshold = max(float(threshold), float(top_score_floor))

    if top_hits < min_evidence_hits:
        return [], {
            "active": True,
            "reason_code": "INSUFFICIENT_EVIDENCE",
            "detail": (
                f"Top row {top_marker} has evidence_hits={top_hits}, required={min_evidence_hits}"
            ),
            "thresholds": thresholds,
        }

    if top_score < effective_threshold:
        return [], {
            "active": True,
            "reason_code": "ROW_SCORE_BELOW_THRESHOLD",
            "detail": (
                f"Top row {top_marker} score={top_score:.6f} threshold={effective_threshold:.6f}"
            ),
            "thresholds": thresholds,
        }

    if len(projection) > 1:
        second_score = float(projection[1].get("score", 0.0))
        margin = top_score - second_score
        if margin < margin_threshold:
            return [], {
                "active": True,
                "reason_code": "LOW_CONFIDENCE_MARGIN",
                "detail": (f"Top-vs-second margin={margin:.6f} required>={margin_threshold:.6f}"),
                "thresholds": thresholds,
            }

    selected: list[dict[str, Any]] = []
    for row in projection:
        marker = str(row.get("row_marker", "")).strip().lower()
        score = float(row.get("score", 0.0))
        hits = int(row.get("evidence_hits", 0))
        min_score = float(thresholds.get(marker, threshold))
        if hits < min_evidence_hits:
            continue
        if score < min_score:
            continue
        selected.append(row)
        if len(selected) >= 3:
            break

    if not selected:
        return [], {
            "active": True,
            "reason_code": "NO_ROW_ABOVE_THRESHOLD",
            "detail": "No row markers satisfied score and evidence thresholds",
            "thresholds": thresholds,
        }

    return selected, {
        "active": False,
        "reason_code": "NONE",
        "detail": "Row projection produced calibrated labels",
        "thresholds": thresholds,
    }


def _apply_corpus_row_policy(
    rows: list[dict[str, Any]], *, query_text: str, corpus: str
) -> list[dict[str, Any]]:
    normalized_corpus = str(corpus).strip().lower()
    if normalized_corpus == "core_docs":
        from retrieval.query_policies.core_docs import apply_target_hint_preference

        return apply_target_hint_preference(rows, query_text=query_text)
    if normalized_corpus == "rust_reference":
        from retrieval.query_policies.rust_reference import apply_intent_path_preference

        return apply_intent_path_preference(rows, query_text=query_text)
    return rows


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
        rewrite = core_rewrite_query_text(
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

    score_definitions = {
        "lexical_score": "Normalized lexical relevance from FTS and token overlap",
        "semantic_score": "Normalized embedding cosine similarity to query",
        "reranker_score": "Normalized cross-encoder reranker relevance",
        "final_score": "",
    }
    fusion_params = {
        "method": normalized_fusion_method,
        "rrf_k": int(resolved_rrf_k),
        "rrf_window": int(resolved_rrf_window),
        "lexical_floor_count": int(resolved_lexical_floor_count),
        "lexical_floor_share": float(resolved_lexical_floor_share),
        "candidate_policy": str(normalized_candidate_policy),
        "rerank_pool_size": int(resolved_hybrid_rerank_pool_size),
        "lexical_min": int(resolved_hybrid_lexical_min),
        "semantic_min": int(resolved_hybrid_semantic_min),
    }
    fusion_debug: dict[str, Any] = {}
    if normalized_fusion_method == HYBRID_FUSION_RRF_V1:
        score_definitions["final_score"] = (
            "Reciprocal rank fusion score across lexical/semantic/reranker lists"
        )
    elif normalized_fusion_method == HYBRID_FUSION_WEIGHTED_V2:
        score_definitions["final_score"] = (
            "Weighted-v2 score "
            f"({WEIGHTED_V2_LEXICAL_WEIGHT:.2f}*lexical + "
            f"{WEIGHTED_V2_SEMANTIC_WEIGHT:.2f}*semantic + "
            f"{WEIGHTED_V2_RERANK_WEIGHT:.2f}*reranker)"
        )
    else:
        score_definitions["final_score"] = (
            f"Weighted score ({LEXICAL_WEIGHT:.2f}*lexical + "
            f"{SEMANTIC_WEIGHT:.2f}*semantic + {RERANK_WEIGHT:.2f}*reranker)"
        )

    if mode == "lexical":
        lexical_started = time.perf_counter()
        lexical_rows = _run_lexical()
        timing["lexical_ms"] += (time.perf_counter() - lexical_started) * 1000.0
        workload["lexical_pool_size"] = int(len(lexical_rows))
        workload["union_pool_size"] = int(len(lexical_rows))
        for row in lexical_rows:
            core_apply_component_scores(
                row,
                lexical_score=float(row.get("lexical_score", 0.0)),
                semantic_score=0.0,
                reranker_score=0.0,
                lexical_weight=LEXICAL_WEIGHT,
                semantic_weight=SEMANTIC_WEIGHT,
                reranker_weight=RERANK_WEIGHT,
            )
        rows = _apply_corpus_row_policy(lexical_rows[:top_k], query_text=query_text, corpus=corpus)
        projection_started = time.perf_counter()
        row_projection_all = _build_row_projection(rows)
        row_projection, abstain = _apply_abstain_policy(
            row_projection_all,
            policy=resolved_row_projection_policy,
        )
        timing["projection_ms"] += (time.perf_counter() - projection_started) * 1000.0
        duration_ms = (time.perf_counter() - started) * 1000.0
        return {
            "requested_mode": mode,
            "executed_mode": mode,
            "degraded": False,
            "semantic_retry_events": semantic_retry_events,
            "score_definitions": score_definitions,
            "candidate_generation": {
                "lexical_pool_size": int(workload["lexical_pool_size"]),
                "semantic_pool_size": int(workload["semantic_pool_size"]),
                "union_pool_size": int(workload["union_pool_size"]),
                "rerank_pool_size": int(workload["rerank_pool_size"]),
                "rerank_doc_count": int(workload["rerank_doc_count"]),
            },
            "query_text": query_text,
            "effective_query_text": effective_query_text,
            "query_rewrite": rewrite,
            "row_marker": row_marker,
            "row_count": len(rows),
            "duration_ms": round(duration_ms, 3),
            "timing": _timing_payload(duration_ms),
            "row_projection": row_projection,
            "row_projection_all": row_projection_all,
            "abstain": abstain,
            "rows": rows,
        }

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
        workload["lexical_pool_size"] = int(len(lexical_rows))
        workload["union_pool_size"] = int(len(lexical_rows))
        for row in lexical_rows:
            core_apply_component_scores(
                row,
                lexical_score=float(row.get("lexical_score", 0.0)),
                semantic_score=0.0,
                reranker_score=0.0,
                lexical_weight=LEXICAL_WEIGHT,
                semantic_weight=SEMANTIC_WEIGHT,
                reranker_weight=RERANK_WEIGHT,
            )
        rows = _apply_corpus_row_policy(lexical_rows[:top_k], query_text=query_text, corpus=corpus)
        projection_started = time.perf_counter()
        row_projection_all = _build_row_projection(rows)
        row_projection, abstain = _apply_abstain_policy(
            row_projection_all,
            policy=resolved_row_projection_policy,
        )
        timing["projection_ms"] += (time.perf_counter() - projection_started) * 1000.0
        duration_ms = (time.perf_counter() - started) * 1000.0
        return {
            "requested_mode": mode,
            "executed_mode": "lexical",
            "degraded": True,
            "degraded_reason": error_code,
            "semantic_retry_events": semantic_retry_events,
            "score_definitions": score_definitions,
            "candidate_generation": {
                "lexical_pool_size": int(workload["lexical_pool_size"]),
                "semantic_pool_size": int(workload["semantic_pool_size"]),
                "union_pool_size": int(workload["union_pool_size"]),
                "rerank_pool_size": int(workload["rerank_pool_size"]),
                "rerank_doc_count": int(workload["rerank_doc_count"]),
            },
            "preflight": preflight,
            "query_text": query_text,
            "effective_query_text": effective_query_text,
            "query_rewrite": rewrite,
            "row_marker": row_marker,
            "row_count": len(rows),
            "duration_ms": round(duration_ms, 3),
            "timing": _timing_payload(duration_ms),
            "row_projection": row_projection,
            "row_projection_all": row_projection_all,
            "abstain": abstain,
            "rows": rows,
        }

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
        workload["lexical_pool_size"] = int(len(lexical_rows))
        workload["union_pool_size"] = int(len(lexical_rows))
        for row in lexical_rows:
            core_apply_component_scores(
                row,
                lexical_score=float(row.get("lexical_score", 0.0)),
                semantic_score=0.0,
                reranker_score=0.0,
                lexical_weight=LEXICAL_WEIGHT,
                semantic_weight=SEMANTIC_WEIGHT,
                reranker_weight=RERANK_WEIGHT,
            )
        rows = _apply_corpus_row_policy(lexical_rows[:top_k], query_text=query_text, corpus=corpus)
        projection_started = time.perf_counter()
        row_projection_all = _build_row_projection(rows)
        row_projection, abstain = _apply_abstain_policy(
            row_projection_all,
            policy=resolved_row_projection_policy,
        )
        timing["projection_ms"] += (time.perf_counter() - projection_started) * 1000.0
        duration_ms = (time.perf_counter() - started) * 1000.0
        return {
            "requested_mode": mode,
            "executed_mode": "lexical",
            "degraded": True,
            "degraded_reason": mapped_code,
            "semantic_retry_events": semantic_retry_events,
            "score_definitions": score_definitions,
            "candidate_generation": {
                "lexical_pool_size": int(workload["lexical_pool_size"]),
                "semantic_pool_size": int(workload["semantic_pool_size"]),
                "union_pool_size": int(workload["union_pool_size"]),
                "rerank_pool_size": int(workload["rerank_pool_size"]),
                "rerank_doc_count": int(workload["rerank_doc_count"]),
            },
            "preflight": preflight,
            "query_text": query_text,
            "effective_query_text": effective_query_text,
            "query_rewrite": rewrite,
            "row_marker": row_marker,
            "row_count": len(rows),
            "duration_ms": round(duration_ms, 3),
            "timing": _timing_payload(duration_ms),
            "row_projection": row_projection,
            "row_projection_all": row_projection_all,
            "abstain": abstain,
            "rows": rows,
        }

    _annotate_rows_with_row_markers(semantic_rows, row_profiles)
    semantic_rows = _filter_rows_by_row_marker(semantic_rows, row_marker)
    workload["semantic_pool_size"] = int(len(semantic_rows))

    if mode == "semantic":
        for row in semantic_rows:
            core_apply_component_scores(
                row,
                lexical_score=0.0,
                semantic_score=float(row.get("semantic_score", 0.0)),
                reranker_score=float(row.get("reranker_score", 0.0)),
                lexical_weight=LEXICAL_WEIGHT,
                semantic_weight=SEMANTIC_WEIGHT,
                reranker_weight=RERANK_WEIGHT,
            )
        semantic_rows.sort(
            key=lambda row: (
                -float(row.get("final_score", 0.0)),
                -float(row.get("reranker_score", 0.0)),
                _row_identity(row),
            )
        )
        rows = _apply_corpus_row_policy(semantic_rows[:top_k], query_text=query_text, corpus=corpus)
        workload["union_pool_size"] = int(len(semantic_rows))
        projection_started = time.perf_counter()
        row_projection_all = _build_row_projection(rows)
        row_projection, abstain = _apply_abstain_policy(
            row_projection_all,
            policy=resolved_row_projection_policy,
        )
        timing["projection_ms"] += (time.perf_counter() - projection_started) * 1000.0
        duration_ms = (time.perf_counter() - started) * 1000.0
        return {
            "requested_mode": mode,
            "executed_mode": mode,
            "degraded": False,
            "semantic_retry_events": semantic_retry_events,
            "score_definitions": score_definitions,
            "candidate_generation": {
                "lexical_pool_size": int(workload["lexical_pool_size"]),
                "semantic_pool_size": int(workload["semantic_pool_size"]),
                "union_pool_size": int(workload["union_pool_size"]),
                "rerank_pool_size": int(workload["rerank_pool_size"]),
                "rerank_doc_count": int(workload["rerank_doc_count"]),
            },
            "preflight": preflight,
            "query_text": query_text,
            "effective_query_text": effective_query_text,
            "query_rewrite": rewrite,
            "row_marker": row_marker,
            "row_count": len(rows),
            "duration_ms": round(duration_ms, 3),
            "timing": _timing_payload(duration_ms),
            "row_projection": row_projection,
            "row_projection_all": row_projection_all,
            "abstain": abstain,
            "rows": rows,
        }

    lexical_started = time.perf_counter()
    lexical_rows = _run_lexical()
    timing["lexical_ms"] += (time.perf_counter() - lexical_started) * 1000.0
    lexical_rows = lexical_rows[:candidate_limit]
    semantic_rows = semantic_rows[:candidate_limit]
    workload["lexical_pool_size"] = int(len(lexical_rows))
    workload["semantic_pool_size"] = int(len(semantic_rows))

    lexical_ids = [_row_identity(row) for row in lexical_rows]
    lexical_values = [float(row.get("lexical_score", 0.0)) for row in lexical_rows]
    lexical_norm = min_max_normalize(lexical_values)
    lexical_score_by_id = dict(zip(lexical_ids, lexical_norm, strict=False))

    merged: dict[str, dict[str, Any]] = {}
    for row in semantic_rows:
        row_id = _row_identity(row)
        merged[row_id] = dict(row)
        merged[row_id]["lexical_score"] = lexical_score_by_id.get(row_id, 0.0)

    for row in lexical_rows:
        row_id = _row_identity(row)
        if row_id not in merged:
            merged[row_id] = dict(row)
            merged[row_id]["semantic_score"] = 0.0
            merged[row_id]["reranker_score"] = 0.0
        merged[row_id]["lexical_score"] = lexical_score_by_id.get(row_id, 0.0)

    candidate_policy_debug: dict[str, Any] = {
        "policy": normalized_candidate_policy,
        "enabled": False,
    }

    def _rerank_with_retries(query: str, documents: list[str]) -> list[float]:
        return _with_semantic_retries(
            "hybrid reranker scoring",
            semantic_retries,
            lambda: rerank_texts(
                config=semantic_config,
                query_text=query,
                documents=documents,
            ),
            telemetry=semantic_retry_events,
        )

    if normalized_candidate_policy == HYBRID_CANDIDATE_POLICY_V2:
        candidate_policy_debug = core_apply_hybrid_candidate_policy_v2_rerank(
            merged_rows=merged,
            lexical_rows=lexical_rows,
            semantic_rows=semantic_rows,
            query_text=effective_query_text,
            top_k=top_k,
            rerank_pool_size=resolved_hybrid_rerank_pool_size,
            lexical_min=resolved_hybrid_lexical_min,
            semantic_min=resolved_hybrid_semantic_min,
            row_identity=_row_identity,
            rerank_documents=_rerank_with_retries,
            normalize_scores=min_max_normalize,
            timing=timing,
            workload=workload,
        )
    else:
        candidate_policy_debug = core_apply_hybrid_lexical_floor_rerank(
            merged_rows=merged,
            lexical_rows=lexical_rows,
            semantic_rows=semantic_rows,
            query_text=effective_query_text,
            top_k=top_k,
            floor_count=resolved_lexical_floor_count,
            floor_share=resolved_lexical_floor_share,
            row_identity=_row_identity,
            rerank_documents=_rerank_with_retries,
            normalize_scores=min_max_normalize,
            timing=timing,
            workload=workload,
        )

    hybrid_rows: list[dict[str, Any]] = []
    if normalized_fusion_method == HYBRID_FUSION_RRF_V1:
        hybrid_rows, fusion_debug = core_apply_rrf_hybrid_scores(
            merged_rows=merged,
            lexical_rows=lexical_rows,
            semantic_rows=semantic_rows,
            rrf_k=resolved_rrf_k,
            rrf_window=resolved_rrf_window,
            row_identity=_row_identity,
        )
        fusion_debug["candidate_policy"] = candidate_policy_debug
    else:
        use_weighted_v2 = normalized_fusion_method == HYBRID_FUSION_WEIGHTED_V2
        for row in merged.values():
            if use_weighted_v2:
                core_apply_component_scores(
                    row,
                    lexical_score=float(row.get("lexical_score", 0.0)),
                    semantic_score=float(row.get("semantic_score", 0.0)),
                    reranker_score=float(row.get("reranker_score", 0.0)),
                    lexical_weight=WEIGHTED_V2_LEXICAL_WEIGHT,
                    semantic_weight=WEIGHTED_V2_SEMANTIC_WEIGHT,
                    reranker_weight=WEIGHTED_V2_RERANK_WEIGHT,
                )
            else:
                core_apply_component_scores(
                    row,
                    lexical_score=float(row.get("lexical_score", 0.0)),
                    semantic_score=float(row.get("semantic_score", 0.0)),
                    reranker_score=float(row.get("reranker_score", 0.0)),
                    lexical_weight=LEXICAL_WEIGHT,
                    semantic_weight=SEMANTIC_WEIGHT,
                    reranker_weight=RERANK_WEIGHT,
                )
            hybrid_rows.append(row)

        hybrid_rows.sort(
            key=lambda row: (
                -float(row.get("final_score", 0.0)),
                -float(row.get("reranker_score", 0.0)),
                _row_identity(row),
            )
        )
        if candidate_policy_debug:
            fusion_debug["candidate_policy"] = candidate_policy_debug
    rows = _apply_corpus_row_policy(hybrid_rows[:top_k], query_text=query_text, corpus=corpus)
    workload["union_pool_size"] = int(len(merged))
    projection_started = time.perf_counter()
    row_projection_all = _build_row_projection(rows)
    row_projection, abstain = _apply_abstain_policy(
        row_projection_all,
        policy=resolved_row_projection_policy,
    )
    timing["projection_ms"] += (time.perf_counter() - projection_started) * 1000.0
    duration_ms = (time.perf_counter() - started) * 1000.0
    return {
        "requested_mode": mode,
        "executed_mode": mode,
        "degraded": False,
        "semantic_retry_events": semantic_retry_events,
        "score_definitions": score_definitions,
        "candidate_generation": {
            "lexical_pool_size": int(workload["lexical_pool_size"]),
            "semantic_pool_size": int(workload["semantic_pool_size"]),
            "union_pool_size": int(workload["union_pool_size"]),
            "rerank_pool_size": int(workload["rerank_pool_size"]),
            "rerank_doc_count": int(workload["rerank_doc_count"]),
        },
        "preflight": preflight,
        "query_text": query_text,
        "effective_query_text": effective_query_text,
        "query_rewrite": rewrite,
        "fusion_method": normalized_fusion_method,
        "fusion_params": fusion_params,
        "fusion_debug": fusion_debug,
        "fusion_weights": {
            "lexical": (
                WEIGHTED_V2_LEXICAL_WEIGHT
                if normalized_fusion_method == HYBRID_FUSION_WEIGHTED_V2
                else LEXICAL_WEIGHT
            ),
            "semantic": (
                WEIGHTED_V2_SEMANTIC_WEIGHT
                if normalized_fusion_method == HYBRID_FUSION_WEIGHTED_V2
                else SEMANTIC_WEIGHT
            ),
            "reranker": (
                WEIGHTED_V2_RERANK_WEIGHT
                if normalized_fusion_method == HYBRID_FUSION_WEIGHTED_V2
                else RERANK_WEIGHT
            ),
        },
        "row_marker": row_marker,
        "row_count": len(rows),
        "duration_ms": round(duration_ms, 3),
        "timing": _timing_payload(duration_ms),
        "row_projection": row_projection,
        "row_projection_all": row_projection_all,
        "abstain": abstain,
        "rows": rows,
    }


def _without_score_breakdown(result: dict[str, Any]) -> dict[str, Any]:
    projected = dict(result)
    projected.pop("row_projection", None)
    projected.pop("row_projection_all", None)

    rows = []
    for row in result.get("rows", []):
        if not isinstance(row, dict):
            continue
        rows.append({key: value for key, value in row.items() if key not in SCORE_FIELDS})
    projected["rows"] = rows
    return projected


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
