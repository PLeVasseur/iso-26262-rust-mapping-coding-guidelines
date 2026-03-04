from __future__ import annotations

import argparse

from retrieval.core.profile import (
    DEFAULT_HYBRID_RRF_K,
    HYBRID_CANDIDATE_POLICIES,
    HYBRID_CANDIDATE_POLICY_LEGACY,
    HYBRID_FUSION_METHODS,
    HYBRID_FUSION_WEIGHTED_V1,
)
from retrieval.corpora.registry import list_supported_corpora


def parse_args(*, default_top_k: int, default_candidate_limit: int) -> argparse.Namespace:
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
        default=default_top_k,
        help="Maximum number of retrieval rows returned",
    )
    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=default_candidate_limit,
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
