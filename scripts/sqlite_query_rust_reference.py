#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from semantic_backend_client import (
    SemanticBackendConfig,
    SemanticBackendError,
    check_semantic_backend,
    embed_texts,
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
TOKEN_RE = re.compile(r"[a-z0-9_]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "do",
    "does",
    "for",
    "how",
    "in",
    "is",
    "it",
    "kinds",
    "of",
    "on",
    "or",
    "rust",
    "that",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "available",
    "features",
    "feature",
    "language",
    "programming",
    "support",
    "supports",
    "techniques",
    "with",
    "why",
}
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


class ModeExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
HYBRID_FUSION_WEIGHTED_V1 = "weighted-v1"
HYBRID_FUSION_WEIGHTED_V2 = "weighted-v2"
HYBRID_FUSION_RRF_V1 = "rrf-v1"
HYBRID_FUSION_METHODS = (
    HYBRID_FUSION_WEIGHTED_V1,
    HYBRID_FUSION_WEIGHTED_V2,
    HYBRID_FUSION_RRF_V1,
)
DEFAULT_HYBRID_RRF_K = 60
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
ROW_PROJECTION_MIN_EVIDENCE_HITS = 1
ROW_PROJECTION_MARGIN = 0.005

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


def _load_rewrite_rules(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise GuardrailError("Rewrite rules payload must be a mapping")
    return payload


def _rewrite_query_text(
    *,
    query_text: str,
    row_marker: str,
    mode: str,
    rewrite_mode: str,
    rewrite_rules_path: Path,
) -> dict[str, Any]:
    normalized_mode = str(rewrite_mode).strip().lower() or "auto"
    original = " ".join(str(query_text).split())
    if normalized_mode == "off":
        return {
            "enabled": False,
            "strategy_tags": ["rewrite-disabled"],
            "rules_path": str(rewrite_rules_path),
            "original_query": original,
            "rewritten_query": original,
            "added_terms": [],
        }

    rules = _load_rewrite_rules(rewrite_rules_path)
    strategy = str(rules.get("strategy", "rewrite-v1")).strip() or "rewrite-v1"
    token_expansions_raw = rules.get("token_expansions") or {}
    row_terms_raw = rules.get("row_marker_terms") or {}
    mode_terms_raw = rules.get("mode_terms") or {}

    token_expansions = {
        str(token).strip().lower(): [
            str(term).strip().lower() for term in list(values or []) if str(term).strip()
        ]
        for token, values in token_expansions_raw.items()
        if str(token).strip()
    }
    row_terms = {
        str(marker).strip().lower(): [
            str(term).strip().lower() for term in list(values or []) if str(term).strip()
        ]
        for marker, values in row_terms_raw.items()
        if str(marker).strip()
    }
    mode_terms = {
        str(mode_name).strip().lower(): [
            str(term).strip().lower() for term in list(values or []) if str(term).strip()
        ]
        for mode_name, values in mode_terms_raw.items()
        if str(mode_name).strip()
    }

    tokens_in_order = [match.group(0).lower() for match in TOKEN_RE.finditer(original.lower())]
    seen = set(tokens_in_order)
    added_terms: list[str] = []
    strategy_tags = [strategy]

    for token in tokens_in_order:
        for term in token_expansions.get(token, []):
            if term in seen:
                continue
            seen.add(term)
            added_terms.append(term)
    if added_terms:
        strategy_tags.append("token-expansion")

    scoped_marker = str(row_marker).strip().lower()
    marker_terms_added = False
    if scoped_marker in row_terms:
        for term in row_terms[scoped_marker]:
            if term in seen:
                continue
            seen.add(term)
            added_terms.append(term)
            marker_terms_added = True
    if marker_terms_added:
        strategy_tags.append("row-marker-terms")

    mode_terms_added = False
    for term in mode_terms.get(str(mode).strip().lower(), []):
        if term in seen:
            continue
        seen.add(term)
        added_terms.append(term)
        mode_terms_added = True
    if mode_terms_added:
        strategy_tags.append("mode-terms")

    rewritten = " ".join(tokens_in_order + added_terms).strip() or original
    return {
        "enabled": True,
        "strategy_tags": strategy_tags,
        "rules_path": str(rewrite_rules_path),
        "original_query": original,
        "rewritten_query": rewritten,
        "added_terms": added_terms,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only query wrapper for rust_reference.sqlite"
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
        default=os.environ.get("RUST_REF_HYBRID_FUSION_METHOD", HYBRID_FUSION_WEIGHTED_V1),
        help="Hybrid fusion method",
    )
    parser.add_argument(
        "--hybrid-rrf-k",
        type=int,
        default=int(os.environ.get("RUST_REF_HYBRID_RRF_K", str(DEFAULT_HYBRID_RRF_K))),
        help="RRF rank constant k for --hybrid-fusion-method rrf-v1",
    )
    parser.add_argument(
        "--hybrid-rrf-window",
        type=int,
        default=0,
        help="Optional rank window for RRF (0 means auto max(top_k*8,64))",
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
        help="Reranker model identifier",
    )
    parser.add_argument(
        "--semantic-timeout-sec",
        type=float,
        default=float(os.environ.get("RUST_REF_SEMANTIC_TIMEOUT_SEC", "60.0")),
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
        default=bool(int(os.environ.get("RUST_REF_ALLOW_ONLINE_CORPUS_EMBEDDING", "0"))),
        help=(
            "Allow semantic query path to embed missing corpus rows on demand "
            "(disabled by default; prefer materialize-first)"
        ),
    )
    parser.add_argument(
        "--db-path",
        default=".cache/sqlite_kb/current/rust_reference.sqlite",
        help="Path to rust_reference.sqlite",
    )
    parser.add_argument(
        "--contract-path",
        default="config/sqlite_query_contracts/rust_reference_chunk.yaml",
        help="Path to rust_reference query contract YAML",
    )
    parser.add_argument(
        "--query-log-root",
        default=".cache/sqlite_kb/query_logs/rust_reference",
        help="Directory used for query audit logs",
    )
    parser.add_argument(
        "--rewrite-mode",
        choices=("auto", "off"),
        default=os.environ.get("RUST_REF_REWRITE_MODE", "auto"),
        help="Deterministic query rewrite mode",
    )
    parser.add_argument(
        "--rewrite-rules-path",
        default=DEFAULT_REWRITE_RULES_PATH,
        help="Path to deterministic query rewrite rules YAML",
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


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in (match.group(0) for match in TOKEN_RE.finditer(text.lower()))
        if len(token) >= 3 and token not in STOPWORDS
    }


def _tokenize_raw(text: str) -> set[str]:
    return {
        token
        for token in (match.group(0) for match in TOKEN_RE.finditer(text.lower()))
        if len(token) >= 3
    }


def _row_identity(row: dict[str, Any]) -> str:
    candidate = str(row.get("chunk_uid", "")).strip()
    if candidate:
        return candidate
    return str(row.get("statement_id", "")).strip()


def _apply_component_scores(
    row: dict[str, Any],
    *,
    lexical_score: float,
    semantic_score: float,
    reranker_score: float,
) -> None:
    final_score = (
        (LEXICAL_WEIGHT * float(lexical_score))
        + (SEMANTIC_WEIGHT * float(semantic_score))
        + (RERANK_WEIGHT * float(reranker_score))
    )
    row["lexical_score"] = float(lexical_score)
    row["semantic_score"] = float(semantic_score)
    row["reranker_score"] = float(reranker_score)
    row["final_score"] = float(final_score)
    row["relevance_score"] = float(final_score)


def _apply_component_scores_with_weights(
    row: dict[str, Any],
    *,
    lexical_score: float,
    semantic_score: float,
    reranker_score: float,
    lexical_weight: float,
    semantic_weight: float,
    reranker_weight: float,
) -> None:
    final_score = (
        (float(lexical_weight) * float(lexical_score))
        + (float(semantic_weight) * float(semantic_score))
        + (float(reranker_weight) * float(reranker_score))
    )
    row["lexical_score"] = float(lexical_score)
    row["semantic_score"] = float(semantic_score)
    row["reranker_score"] = float(reranker_score)
    row["final_score"] = float(final_score)
    row["relevance_score"] = float(final_score)


def _build_rank_map(rows: list[dict[str, Any]], *, window: int) -> dict[str, int]:
    rank_map: dict[str, int] = {}
    if window <= 0:
        return rank_map
    for rank, row in enumerate(rows[:window], start=1):
        row_id = _row_identity(row)
        if not row_id or row_id in rank_map:
            continue
        rank_map[row_id] = int(rank)
    return rank_map


def _apply_rrf_hybrid_scores(
    *,
    merged_rows: dict[str, dict[str, Any]],
    lexical_rows: list[dict[str, Any]],
    semantic_rows: list[dict[str, Any]],
    rrf_k: int,
    rrf_window: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lexical_rank_map = _build_rank_map(lexical_rows, window=rrf_window)
    semantic_rank_map = _build_rank_map(semantic_rows, window=rrf_window)

    reranker_ranked = sorted(
        semantic_rows,
        key=lambda row: (
            -float(row.get("reranker_score", 0.0)),
            -float(row.get("semantic_score", 0.0)),
            _row_identity(row),
        ),
    )
    reranker_rank_map = _build_rank_map(reranker_ranked, window=rrf_window)

    contribution_counts = {"0": 0, "1": 0, "2": 0, "3": 0}
    hybrid_rows: list[dict[str, Any]] = []
    rank_constant = max(1, int(rrf_k))

    for row in merged_rows.values():
        row_id = _row_identity(row)
        lexical_rank = lexical_rank_map.get(row_id, 0)
        semantic_rank = semantic_rank_map.get(row_id, 0)
        reranker_rank = reranker_rank_map.get(row_id, 0)

        contribution_count = (
            int(bool(lexical_rank)) + int(bool(semantic_rank)) + int(bool(reranker_rank))
        )
        contribution_counts[str(contribution_count)] += 1

        rrf_score = 0.0
        if lexical_rank:
            rrf_score += 1.0 / float(rank_constant + lexical_rank)
        if semantic_rank:
            rrf_score += 1.0 / float(rank_constant + semantic_rank)
        if reranker_rank:
            rrf_score += 1.0 / float(rank_constant + reranker_rank)

        row["lexical_rank"] = int(lexical_rank)
        row["semantic_rank"] = int(semantic_rank)
        row["reranker_rank"] = int(reranker_rank)
        row["rrf_score"] = float(rrf_score)
        row["final_score"] = float(rrf_score)
        row["relevance_score"] = float(rrf_score)
        hybrid_rows.append(row)

    hybrid_rows.sort(
        key=lambda row: (
            -float(row.get("rrf_score", 0.0)),
            int(row.get("reranker_rank", 0)) or 10**9,
            int(row.get("lexical_rank", 0)) or 10**9,
            _row_identity(row),
        )
    )

    return hybrid_rows, {
        "lists_fused": ["lexical", "semantic", "reranker"],
        "contribution_counts": contribution_counts,
    }


def _to_fts_query(query_text: str) -> str:
    tokens = _tokenize(query_text)
    if not tokens:
        tokens = _tokenize_raw(query_text)
    ordered = sorted(tokens)
    if not ordered:
        raise GuardrailError("Query text did not yield searchable lexical tokens")
    return " OR ".join(ordered)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _safe_slug(value: str, fallback: str) -> str:
    tokens = [token for token in re.findall(r"[a-z0-9]+", str(value).lower()) if token]
    if not tokens:
        return fallback
    slug = "-".join(tokens)
    clipped = slug[:80].strip("-")
    return clipped if clipped else fallback


def _resolve_root_relative_path(root: Path, raw: str) -> Path:
    path = Path(str(raw).strip())
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def resolve_review_artifact_path(
    *,
    root: Path,
    mode: str,
    query_text: str,
    prompt_id: str,
    save_response_path: str,
    save_response_dir: str,
) -> Path | None:
    explicit_path = str(save_response_path).strip()
    explicit_dir = str(save_response_dir).strip()
    if explicit_path and explicit_dir:
        raise GuardrailError("Use either --save-response-path or --save-response-dir, not both")

    if not explicit_path and not explicit_dir:
        return None

    if explicit_path:
        return _resolve_root_relative_path(root, explicit_path)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    prompt_token = _safe_slug(prompt_id, "")
    if not prompt_token:
        prompt_token = _safe_slug(query_text, "adhoc")
    filename = f"{stamp}__{prompt_token}__{str(mode).strip().lower()}.json"
    directory = _resolve_root_relative_path(root, explicit_dir or DEFAULT_QUERY_REVIEW_DIR)
    return directory / filename


def build_review_artifact_payload(
    *,
    mode: str,
    query_text: str,
    row_marker: str,
    prompt_id: str,
    top_k: int,
    candidate_limit: int,
    include_score_breakdown: bool,
    allow_degraded: bool,
    db_path: Path,
    contract_path: Path,
    query_log_root: Path,
    semantic_config: SemanticBackendConfig | None,
    semantic_retries: int,
    persist_semantic_cache: bool,
    allow_online_corpus_embedding: bool,
    response: dict[str, Any],
) -> dict[str, Any]:
    semantic_runtime: dict[str, Any] = {}
    if semantic_config is not None:
        semantic_runtime = {
            "base_url": str(semantic_config.base_url),
            "embed_base_url": str(semantic_config.embed_base_url or semantic_config.base_url),
            "rerank_base_url": str(semantic_config.rerank_base_url or semantic_config.base_url),
            "embed_model_id": str(semantic_config.embed_model_id),
            "reranker_model_id": str(semantic_config.reranker_model_id),
            "timeout_sec": float(semantic_config.timeout_sec),
            "semantic_retries": int(semantic_retries),
            "persist_semantic_cache": bool(persist_semantic_cache),
            "allow_online_corpus_embedding": bool(allow_online_corpus_embedding),
        }

    return {
        "schema_version": REVIEW_ARTIFACT_SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "prompt_id": str(prompt_id).strip(),
        "query": {
            "mode": str(mode).strip().lower(),
            "query_text": str(query_text),
            "row_marker": str(row_marker).strip().lower(),
            "top_k": int(top_k),
            "candidate_limit": int(candidate_limit),
            "include_score_breakdown": bool(include_score_breakdown),
            "allow_degraded": bool(allow_degraded),
        },
        "runtime": {
            "db_path": str(db_path),
            "contract_path": str(contract_path),
            "query_log_root": str(query_log_root),
            "backend_profile": str(os.environ.get("RUST_REF_SEMANTIC_BACKEND_PROFILE", "unknown")),
            "semantic": semantic_runtime,
        },
        "response": response,
    }


def persist_review_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ensure_embedding_cache_table(
    db_path: Path, retrieval_contract: RetrievalContractProfile
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


def _split_csv_field(raw: str) -> list[str]:
    values = [value.strip() for value in str(raw).split(",") if value.strip()]
    return sorted(set(values))


def _min_max_normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    lower = min(values)
    upper = max(values)
    if abs(upper - lower) < 1e-12:
        return [1.0 for _ in values]
    return [(value - lower) / (upper - lower) for value in values]


def _dot(left: list[float], right: list[float]) -> float:
    return float(sum(a * b for a, b in zip(left, right, strict=False)))


def _l2_norm(values: list[float]) -> float:
    return math.sqrt(sum(value * value for value in values))


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    left_norm = _l2_norm(left)
    right_norm = _l2_norm(right)
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return _dot(left, right) / (left_norm * right_norm)


def _classify_semantic_error(detail: str) -> str:
    text = str(detail).strip().lower()
    if "timed out" in text:
        return "timeout"
    if "http 404" in text:
        return "http_404"
    if "http " in text:
        return "http"
    if "non-json" in text or "payload" in text:
        return "payload"
    if "request failed" in text:
        return "connection"
    return "unknown"


def _with_semantic_retries(
    description: str,
    retries: int,
    call: Callable[[], Any],
    telemetry: list[dict[str, Any]] | None = None,
) -> Any:
    attempts = max(0, int(retries)) + 1
    last_error: SemanticBackendError | None = None
    attempt_events: list[dict[str, Any]] = []
    total_started = time.perf_counter()
    for attempt in range(attempts):
        attempt_started = time.perf_counter()
        try:
            value = call()
            attempt_duration_ms = (time.perf_counter() - attempt_started) * 1000.0
            attempt_events.append(
                {
                    "attempt": attempt + 1,
                    "status": "pass",
                    "duration_ms": round(float(attempt_duration_ms), 3),
                }
            )
            if telemetry is not None:
                telemetry.append(
                    {
                        "operation": description,
                        "status": "pass",
                        "max_attempts": attempts,
                        "attempts_used": attempt + 1,
                        "retry_count": attempt,
                        "total_duration_ms": round(
                            float((time.perf_counter() - total_started) * 1000.0),
                            3,
                        ),
                        "attempt_events": attempt_events,
                    }
                )
            return value
        except SemanticBackendError as exc:
            last_error = exc
            attempt_duration_ms = (time.perf_counter() - attempt_started) * 1000.0
            detail = str(exc)
            attempt_events.append(
                {
                    "attempt": attempt + 1,
                    "status": "fail",
                    "duration_ms": round(float(attempt_duration_ms), 3),
                    "error": detail,
                    "error_class": _classify_semantic_error(detail),
                }
            )
            if attempt + 1 >= attempts:
                break
            time.sleep(0.2 * (attempt + 1))
    message = str(last_error) if last_error is not None else "unknown semantic backend error"
    if telemetry is not None:
        telemetry.append(
            {
                "operation": description,
                "status": "fail",
                "max_attempts": attempts,
                "attempts_used": attempts,
                "retry_count": max(0, attempts - 1),
                "error": message,
                "error_class": _classify_semantic_error(message),
                "total_duration_ms": round(
                    float((time.perf_counter() - total_started) * 1000.0), 3
                ),
                "attempt_events": attempt_events,
            }
        )
    raise ModeExecutionError(
        code="SEMANTIC_BACKEND_UNAVAILABLE",
        message=f"{description} failed after {attempts} attempts: {message}",
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


def _materialize_common_row(raw_row: dict[str, Any], query_tokens: set[str]) -> dict[str, Any]:
    statement_id = str(raw_row.get("statement_id", raw_row.get("chunk_uid", "")))
    text = str(raw_row.get("statement_text", raw_row.get("chunk_text", "")))
    overlap = len(query_tokens.intersection(_tokenize(text))) if query_tokens else 0
    bm25_raw = float(raw_row.get("bm25_raw", 0.0))
    payload = {
        "statement_id": statement_id,
        "statement_text": text,
        "section_heading": str(raw_row.get("section_heading", "")),
        "source_anchor": str(raw_row.get("source_anchor", "")),
        "source_fetched_at": str(raw_row.get("source_fetched_at", "")),
        "row_markers": _split_csv_field(str(raw_row.get("row_markers", ""))),
        "mechanism_ids": _split_csv_field(str(raw_row.get("mechanism_ids", ""))),
        "mechanism_families": _split_csv_field(str(raw_row.get("mechanism_families", ""))),
        "text_sha256": _sha256_text(text.lower()),
        "bm25_raw": bm25_raw,
        "phrase_match": int(raw_row.get("phrase_match", 0) or 0),
        "token_overlap_count": overlap,
        "lexical_score": -bm25_raw,
    }
    if "chunk_uid" in raw_row or "chunk_text" in raw_row:
        payload["chunk_uid"] = statement_id
        payload["chunk_text"] = text
    return payload


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
    query_tokens = _tokenize(query_text)
    fts_query = _to_fts_query(query_text)

    corpus_by_statement_id = {str(row["statement_id"]): row for row in corpus_rows}

    result = execute_contract_query(
        db_path=db_path,
        contract_path=contract_path,
        query_id=retrieval_contract.lexical_query_id,
        params={"fts_query": fts_query},
        row_limit=row_limit,
        query_log_root=query_log_root,
    )

    if not result["rows"]:
        fallback_tokens = sorted(_tokenize_raw(query_text))
        fallback_query = " OR ".join(fallback_tokens)
        if fallback_query and fallback_query != fts_query:
            result = execute_contract_query(
                db_path=db_path,
                contract_path=contract_path,
                query_id=retrieval_contract.lexical_query_id,
                params={"fts_query": fallback_query},
                row_limit=row_limit,
                query_log_root=query_log_root,
            )

    rows: list[dict[str, Any]] = []
    for match_row in result["rows"]:
        statement_id = str(
            match_row.get(
                retrieval_contract.lexical_id_column,
                match_row.get("statement_id", ""),
            )
        )
        if statement_id not in corpus_by_statement_id:
            continue

        row = dict(corpus_by_statement_id[statement_id])
        row["bm25_raw"] = float(match_row.get("bm25_raw", 0.0))
        row["phrase_match"] = int(query_text.lower() in str(row["statement_text"]).lower())
        row["token_overlap_count"] = len(
            query_tokens.intersection(_tokenize(str(row["statement_text"])))
        )
        rows.append(row)

    if not rows:
        return []

    if query_tokens:
        max_overlap = max(int(row["token_overlap_count"]) for row in rows)
        target_overlap = max(1, int(math.ceil(len(query_tokens) * 0.6)))
        min_overlap_required = min(max_overlap, target_overlap)
        filtered_rows = [
            row for row in rows if int(row["token_overlap_count"]) >= min_overlap_required
        ]
        if filtered_rows:
            rows = filtered_rows

    bm25_values = [-float(row["bm25_raw"]) for row in rows]
    overlap_values = [float(row["token_overlap_count"]) for row in rows]
    bm25_norm = _min_max_normalize(bm25_values)
    overlap_norm = _min_max_normalize(overlap_values)

    for row, bm25_score, overlap_score in zip(rows, bm25_norm, overlap_norm, strict=False):
        row["lexical_score"] = (
            (0.55 * float(bm25_score))
            + (0.35 * float(overlap_score))
            + (0.10 * float(row["phrase_match"]))
        )

    rows.sort(
        key=lambda row: (
            -float(row["lexical_score"]),
            float(row["bm25_raw"]),
            _row_identity(row),
        )
    )
    _annotate_rows_with_row_markers(rows, row_profiles)
    return _filter_rows_by_row_marker(rows, row_marker)


def _load_statement_corpus(
    *,
    db_path: Path,
    contract_path: Path,
    query_log_root: Path,
    retrieval_contract: RetrievalContractProfile | None = None,
    max_rows: int | None = None,
) -> list[dict[str, Any]]:
    profile = retrieval_contract or _resolve_retrieval_contract_profile(contract_path)
    rows: list[dict[str, Any]] = []
    statement_id_after = ""

    while True:
        result = execute_contract_query(
            db_path=db_path,
            contract_path=contract_path,
            query_id=profile.corpus_query_id,
            params={profile.corpus_cursor_param: statement_id_after},
            row_limit=FULL_CORPUS_PAGE_LIMIT,
            query_log_root=query_log_root,
        )
        batch = [_materialize_common_row(row, set()) for row in result["rows"]]
        if not batch:
            break

        rows.extend(batch)
        if max_rows is not None and len(rows) >= int(max_rows):
            return rows[: int(max_rows)]

        if len(batch) < FULL_CORPUS_PAGE_LIMIT:
            break
        statement_id_after = str(batch[-1]["statement_id"])

    return rows


def _load_table1_row_requirements(
    *,
    db_path: Path,
    contract_path: Path,
    query_log_root: Path,
    retrieval_contract: RetrievalContractProfile | None = None,
) -> list[dict[str, Any]]:
    profile = retrieval_contract or _resolve_retrieval_contract_profile(contract_path)
    result = execute_contract_query(
        db_path=db_path,
        contract_path=contract_path,
        query_id=profile.row_requirements_query_id,
        params={"row_marker": ""},
        row_limit=20,
        query_log_root=query_log_root,
    )

    profiles: list[dict[str, Any]] = []
    for row in result["rows"]:
        row_marker = str(row.get("row_marker", "")).strip().lower()
        requirement_text = str(row.get("requirement_text", "")).strip()
        profile_terms = _split_csv_field(str(row.get("profile_terms", "")))
        if not row_marker:
            continue

        tokens = _tokenize(requirement_text)
        for term in profile_terms:
            tokens.update(_tokenize(term))
        if not tokens:
            tokens = _tokenize_raw(requirement_text)
        if not tokens:
            for term in profile_terms:
                tokens.update(_tokenize_raw(term))
        profiles.append(
            {
                "row_marker": row_marker,
                "requirement_text": requirement_text,
                "profile_terms": profile_terms,
                "tokens": tokens,
            }
        )

    profiles.sort(key=lambda value: str(value["row_marker"]))
    return profiles


def _derive_row_marker_scores(
    statement_text: str,
    row_profiles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    statement_tokens = _tokenize(statement_text)
    if not statement_tokens:
        statement_tokens = _tokenize_raw(statement_text)
    if not statement_tokens or not row_profiles:
        return []

    matches: list[dict[str, Any]] = []
    for profile in row_profiles:
        row_marker = str(profile.get("row_marker", "")).strip().lower()
        row_tokens = set(profile.get("tokens", set()))
        if not row_marker or not row_tokens:
            continue

        overlap = statement_tokens.intersection(row_tokens)
        overlap_count = len(overlap)
        if overlap_count <= 0:
            continue

        score = float(overlap_count) / math.sqrt(float(len(row_tokens)))
        matches.append(
            {
                "row_marker": row_marker,
                "score": float(score),
                "overlap_count": int(overlap_count),
            }
        )

    if not matches:
        return []

    matches.sort(
        key=lambda row: (
            -float(row["score"]),
            -int(row["overlap_count"]),
            str(row["row_marker"]),
        )
    )

    top_score = float(matches[0]["score"])
    threshold = max(0.20, top_score * 0.72)
    selected = [row for row in matches if float(row["score"]) >= threshold][:3]
    for row in selected:
        row["score"] = round(float(row["score"]), 6)
    return selected


def _annotate_rows_with_row_markers(
    rows: list[dict[str, Any]],
    row_profiles: list[dict[str, Any]],
) -> None:
    for row in rows:
        statement_text = str(row.get("statement_text", ""))
        derived = _derive_row_marker_scores(statement_text, row_profiles)
        row["row_marker_scores"] = derived
        row["row_markers"] = [str(item["row_marker"]) for item in derived]


def _filter_rows_by_row_marker(rows: list[dict[str, Any]], row_marker: str) -> list[dict[str, Any]]:
    scoped = str(row_marker).strip().lower()
    if not scoped:
        return rows
    return [
        row
        for row in rows
        if scoped in {str(marker).strip().lower() for marker in row.get("row_markers", [])}
    ]


def _load_embedding_cache(
    *,
    db_path: Path,
    retrieval_contract: RetrievalContractProfile,
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


def _persist_embedding_cache(
    *,
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
                        float(_l2_norm([float(value) for value in row["embedding"]])),
                        _utc_now(),
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
                        float(_l2_norm([float(value) for value in row["embedding"]])),
                        _utc_now(),
                        str(row.get("source_fetched_at", "")),
                    )
                    for row in rows
                ],
            )
        connection.commit()
    finally:
        connection.close()


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
    if not corpus_rows:
        return []

    query_embed_started = time.perf_counter()
    query_embedding = _with_semantic_retries(
        "query embedding",
        retries,
        lambda: embed_texts(config, [query_text]),
        telemetry=retry_events,
    )[0]
    if timing is not None:
        timing["semantic_embed_ms"] = timing.get("semantic_embed_ms", 0.0) + (
            (time.perf_counter() - query_embed_started) * 1000.0
        )

    embeddings_by_statement_id = _load_embedding_cache(
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
                    "Run sqlite_materialize_rust_reference_embeddings.py first "
                    "or set --allow-online-corpus-embedding for local experimentation."
                ),
            )

        batch_size = 32
        persisted_payloads: list[dict[str, Any]] = []
        for offset in range(0, len(missing_rows), batch_size):
            batch_rows = missing_rows[offset : offset + batch_size]
            batch_texts = [str(row["statement_text"]) for row in batch_rows]
            statement_embed_started = time.perf_counter()
            vectors = _with_semantic_retries(
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
            _persist_embedding_cache(
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
        semantic_score = _cosine_similarity(query_embedding, vector)
        enriched = dict(row)
        enriched["semantic_score_raw"] = semantic_score
        scored_rows.append(enriched)
        semantic_scores.append(semantic_score)

    if not scored_rows:
        return []

    normalized_semantic = _min_max_normalize(semantic_scores)
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
    reranker_scores_raw = _with_semantic_retries(
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
    reranker_scores = _min_max_normalize([float(value) for value in reranker_scores_raw])

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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not projection:
        return [], {
            "active": True,
            "reason_code": "NO_ROW_SIGNAL",
            "detail": "No row marker evidence was generated from retrieved chunks",
            "thresholds": ROW_PROJECTION_THRESHOLDS,
        }

    top = projection[0]
    top_marker = str(top.get("row_marker", "")).strip().lower()
    top_score = float(top.get("score", 0.0))
    top_hits = int(top.get("evidence_hits", 0))
    threshold = float(ROW_PROJECTION_THRESHOLDS.get(top_marker, 0.015))

    if top_hits < ROW_PROJECTION_MIN_EVIDENCE_HITS:
        return [], {
            "active": True,
            "reason_code": "INSUFFICIENT_EVIDENCE",
            "detail": (
                f"Top row {top_marker} has evidence_hits={top_hits}, "
                f"required={ROW_PROJECTION_MIN_EVIDENCE_HITS}"
            ),
            "thresholds": ROW_PROJECTION_THRESHOLDS,
        }

    if top_score < threshold:
        return [], {
            "active": True,
            "reason_code": "ROW_SCORE_BELOW_THRESHOLD",
            "detail": f"Top row {top_marker} score={top_score:.6f} threshold={threshold:.6f}",
            "thresholds": ROW_PROJECTION_THRESHOLDS,
        }

    if len(projection) > 1:
        second_score = float(projection[1].get("score", 0.0))
        margin = top_score - second_score
        if margin < ROW_PROJECTION_MARGIN:
            return [], {
                "active": True,
                "reason_code": "LOW_CONFIDENCE_MARGIN",
                "detail": (
                    f"Top-vs-second margin={margin:.6f} required>={ROW_PROJECTION_MARGIN:.6f}"
                ),
                "thresholds": ROW_PROJECTION_THRESHOLDS,
            }

    selected: list[dict[str, Any]] = []
    for row in projection:
        marker = str(row.get("row_marker", "")).strip().lower()
        score = float(row.get("score", 0.0))
        hits = int(row.get("evidence_hits", 0))
        min_score = float(ROW_PROJECTION_THRESHOLDS.get(marker, threshold))
        if hits < ROW_PROJECTION_MIN_EVIDENCE_HITS:
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
            "thresholds": ROW_PROJECTION_THRESHOLDS,
        }

    return selected, {
        "active": False,
        "reason_code": "NONE",
        "detail": "Row projection produced calibrated labels",
        "thresholds": ROW_PROJECTION_THRESHOLDS,
    }


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
) -> dict[str, Any]:
    started = time.perf_counter()
    semantic_retry_events: list[dict[str, Any]] = []
    row_marker = row_marker.strip().lower()
    top_k = max(1, int(top_k))
    candidate_limit = max(top_k, int(candidate_limit))
    normalized_fusion_method = (
        str(hybrid_fusion_method).strip().lower() or HYBRID_FUSION_WEIGHTED_V1
    )
    if normalized_fusion_method not in HYBRID_FUSION_METHODS:
        raise GuardrailError(
            f"Unsupported hybrid fusion method: {hybrid_fusion_method}. "
            f"Expected one of {', '.join(HYBRID_FUSION_METHODS)}"
        )
    resolved_rrf_k = max(1, int(hybrid_rrf_k))
    resolved_rrf_window = int(hybrid_rrf_window)
    if resolved_rrf_window <= 0:
        resolved_rrf_window = max(top_k * 8, 64)

    timing: dict[str, float] = {
        "preflight_ms": 0.0,
        "lexical_ms": 0.0,
        "semantic_embed_ms": 0.0,
        "semantic_score_ms": 0.0,
        "rerank_ms": 0.0,
        "projection_ms": 0.0,
    }
    workload: dict[str, int] = {
        "lexical_pool_size": 0,
        "semantic_pool_size": 0,
        "union_pool_size": 0,
        "rerank_pool_size": 0,
        "rerank_doc_count": 0,
    }

    def _timing_payload(total_case_ms: float) -> dict[str, float]:
        payload = {name: round(float(value), 3) for name, value in timing.items()}
        payload["total_case_ms"] = round(float(total_case_ms), 3)
        return payload

    rewrite_path = rewrite_rules_path or (
        Path(__file__).resolve().parents[1] / DEFAULT_REWRITE_RULES_PATH
    )
    rewrite = _rewrite_query_text(
        query_text=query_text,
        row_marker=row_marker,
        mode=mode,
        rewrite_mode=rewrite_mode,
        rewrite_rules_path=rewrite_path,
    )
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
            _apply_component_scores(
                row,
                lexical_score=float(row.get("lexical_score", 0.0)),
                semantic_score=0.0,
                reranker_score=0.0,
            )
        rows = lexical_rows[:top_k]
        projection_started = time.perf_counter()
        row_projection_all = _build_row_projection(rows)
        row_projection, abstain = _apply_abstain_policy(row_projection_all)
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
            _apply_component_scores(
                row,
                lexical_score=float(row.get("lexical_score", 0.0)),
                semantic_score=0.0,
                reranker_score=0.0,
            )
        rows = lexical_rows[:top_k]
        projection_started = time.perf_counter()
        row_projection_all = _build_row_projection(rows)
        row_projection, abstain = _apply_abstain_policy(row_projection_all)
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

    _ensure_embedding_cache_table(db_path, retrieval_contract)

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
            _apply_component_scores(
                row,
                lexical_score=float(row.get("lexical_score", 0.0)),
                semantic_score=0.0,
                reranker_score=0.0,
            )
        rows = lexical_rows[:top_k]
        projection_started = time.perf_counter()
        row_projection_all = _build_row_projection(rows)
        row_projection, abstain = _apply_abstain_policy(row_projection_all)
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
            _apply_component_scores(
                row,
                lexical_score=0.0,
                semantic_score=float(row.get("semantic_score", 0.0)),
                reranker_score=float(row.get("reranker_score", 0.0)),
            )
        semantic_rows.sort(
            key=lambda row: (
                -float(row.get("final_score", 0.0)),
                -float(row.get("reranker_score", 0.0)),
                _row_identity(row),
            )
        )
        rows = semantic_rows[:top_k]
        workload["union_pool_size"] = int(len(semantic_rows))
        projection_started = time.perf_counter()
        row_projection_all = _build_row_projection(rows)
        row_projection, abstain = _apply_abstain_policy(row_projection_all)
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
    lexical_norm = _min_max_normalize(lexical_values)
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

    hybrid_rows: list[dict[str, Any]] = []
    if normalized_fusion_method == HYBRID_FUSION_RRF_V1:
        hybrid_rows, fusion_debug = _apply_rrf_hybrid_scores(
            merged_rows=merged,
            lexical_rows=lexical_rows,
            semantic_rows=semantic_rows,
            rrf_k=resolved_rrf_k,
            rrf_window=resolved_rrf_window,
        )
    else:
        use_weighted_v2 = normalized_fusion_method == HYBRID_FUSION_WEIGHTED_V2
        for row in merged.values():
            if use_weighted_v2:
                _apply_component_scores_with_weights(
                    row,
                    lexical_score=float(row.get("lexical_score", 0.0)),
                    semantic_score=float(row.get("semantic_score", 0.0)),
                    reranker_score=float(row.get("reranker_score", 0.0)),
                    lexical_weight=WEIGHTED_V2_LEXICAL_WEIGHT,
                    semantic_weight=WEIGHTED_V2_SEMANTIC_WEIGHT,
                    reranker_weight=WEIGHTED_V2_RERANK_WEIGHT,
                )
            else:
                _apply_component_scores(
                    row,
                    lexical_score=float(row.get("lexical_score", 0.0)),
                    semantic_score=float(row.get("semantic_score", 0.0)),
                    reranker_score=float(row.get("reranker_score", 0.0)),
                )
            hybrid_rows.append(row)

        hybrid_rows.sort(
            key=lambda row: (
                -float(row.get("final_score", 0.0)),
                -float(row.get("reranker_score", 0.0)),
                _row_identity(row),
            )
        )
    rows = hybrid_rows[:top_k]
    workload["union_pool_size"] = int(len(merged))
    projection_started = time.perf_counter()
    row_projection_all = _build_row_projection(rows)
    row_projection, abstain = _apply_abstain_policy(row_projection_all)
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
    root = Path(__file__).resolve().parents[1]

    db_path = (root / args.db_path).resolve()
    contract_path = (root / args.contract_path).resolve()
    query_log_root = (root / args.query_log_root).resolve()

    query_text = str(getattr(args, "query_text", "")).strip()
    allow_degraded = bool(getattr(args, "allow_degraded", False))
    if not allow_degraded:
        allow_degraded = os.environ.get("RUST_REF_ALLOW_DEGRADED", "0") == "1"

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
                rewrite_mode=str(args.rewrite_mode),
                rewrite_rules_path=(root / str(args.rewrite_rules_path)).resolve(),
                hybrid_fusion_method=str(args.hybrid_fusion_method),
                hybrid_rrf_k=int(args.hybrid_rrf_k),
                hybrid_rrf_window=int(args.hybrid_rrf_window),
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
