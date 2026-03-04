from __future__ import annotations

import argparse
from pathlib import Path

from retrieval.corpora.registry import list_supported_corpora
from retrieval.ingest.registry import list_ingest_strategies


def parse_build_args(
    *,
    default_extractor_db: Path,
    default_table_node_id: str,
    default_reference_cache_dir: str,
    default_reference_repo_url: str,
    default_retrieval_mode: str,
    retrieval_corpus_values: tuple[str, ...],
    default_retrieval_corpus: str,
    default_semantic_profile_version: str,
    default_embedding_model_id: str,
    default_embedding_model_revision: str,
    default_embedding_model_license: str,
    default_embedding_dim: int,
    default_reranker_model_id: str,
    default_reranker_model_revision: str,
    default_reranker_model_license: str,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build rust_reference.sqlite (Rank 1)")
    parser.add_argument(
        "--corpus",
        choices=list_supported_corpora(),
        default="rust_reference",
        help="Corpus adapter used to resolve build runner",
    )
    parser.add_argument(
        "--db-path",
        default=".cache/sqlite_kb/current/rust_reference.sqlite",
        help="Target path for active rust_reference.sqlite",
    )
    parser.add_argument(
        "--snapshot-root",
        default=".cache/sqlite_kb/snapshots/rust_reference",
        help="Directory where immutable snapshots are copied",
    )
    parser.add_argument(
        "--manifest-path",
        default="data/sqlite_kb_manifest.yaml",
        help="Manifest path tracking current snapshot metadata",
    )
    parser.add_argument(
        "--report-root",
        default=".cache/sqlite_kb/reports/rust_reference",
        help="Directory for validation report artifacts",
    )
    parser.add_argument(
        "--extractor-db",
        default=str(default_extractor_db),
        help="Path to ISO 26262 extractor index sqlite",
    )
    parser.add_argument(
        "--table-node-id",
        default=default_table_node_id,
        help="Canonical table node id for ISO 26262 Part 6 Table 1",
    )
    parser.add_argument(
        "--reference-source-dir",
        default=None,
        help="Optional local rust reference source directory (expects src/SUMMARY.md)",
    )
    parser.add_argument(
        "--reference-cache-dir",
        default=default_reference_cache_dir,
        help="Cache path for cloned rust-lang/reference repository",
    )
    parser.add_argument(
        "--reference-repo-url",
        default=default_reference_repo_url,
        help="Git URL for Rust Reference repository",
    )
    parser.add_argument(
        "--reference-revision",
        default=None,
        help="Pinned revision/commit/tag for rust reference checkout (required)",
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip git fetch before resolving revision",
    )
    parser.add_argument(
        "--min-sections",
        type=int,
        default=20,
        help="Minimum extracted section count required by validation",
    )
    parser.add_argument(
        "--min-statements",
        type=int,
        default=50,
        help="Minimum extracted statement count required by validation",
    )
    parser.add_argument(
        "--min-mechanisms",
        type=int,
        default=6,
        help="Minimum extracted mechanism count required by validation",
    )
    parser.add_argument(
        "--retrieval-mode",
        choices=("hybrid", "lexical"),
        default=default_retrieval_mode,
        help="Row-mechanism ranking mode",
    )
    parser.add_argument(
        "--retrieval-corpus",
        choices=retrieval_corpus_values,
        default=default_retrieval_corpus,
        help="Retrieval corpus lane to materialize (statement or chunk)",
    )
    parser.add_argument(
        "--semantic-profile-version",
        default=default_semantic_profile_version,
        help="Semantic score profile/version label",
    )
    parser.add_argument(
        "--embedding-model-id",
        default=default_embedding_model_id,
        help="Embedding model identifier used for semantic retrieval metadata",
    )
    parser.add_argument(
        "--embedding-model-revision",
        default=default_embedding_model_revision,
        help="Embedding model revision metadata",
    )
    parser.add_argument(
        "--embedding-model-license",
        default=default_embedding_model_license,
        help="Embedding model license metadata",
    )
    parser.add_argument(
        "--embedding-dim",
        type=int,
        default=default_embedding_dim,
        help="Embedding vector dimension metadata",
    )
    parser.add_argument(
        "--reranker-model-id",
        default=default_reranker_model_id,
        help="Reranker model identifier used for semantic retrieval metadata",
    )
    parser.add_argument(
        "--reranker-model-revision",
        default=default_reranker_model_revision,
        help="Reranker model revision metadata",
    )
    parser.add_argument(
        "--reranker-model-license",
        default=default_reranker_model_license,
        help="Reranker model license metadata",
    )
    parser.add_argument(
        "--ingest-strategy",
        choices=list_ingest_strategies(),
        default="rust_md_v1",
        help="Ingest strategy id for source cleaning/chunking",
    )
    parser.add_argument(
        "--chunk-target-min-tokens",
        type=int,
        default=150,
        help="Minimum target tokens per generated chunk",
    )
    parser.add_argument(
        "--chunk-target-max-tokens",
        type=int,
        default=500,
        help="Maximum target tokens per generated chunk",
    )
    parser.add_argument(
        "--chunk-overlap-percent",
        type=float,
        default=0.0,
        help="Chunk overlap ratio [0.0, 0.45] for source-structure chunking",
    )
    parser.add_argument(
        "--allow-provenance-mismatch",
        action="store_true",
        help="Record build run even if provenance mismatch override is active",
    )
    parser.add_argument(
        "--guidelines-repo-root",
        default="",
        help="Path to sibling safety-critical-rust-coding-guidelines checkout",
    )
    parser.add_argument(
        "--guidelines-repo-revision",
        default="",
        help="Pinned revision SHA for guidelines_repo corpus",
    )
    parser.add_argument(
        "--guidelines-exemplar-id",
        action="append",
        default=[],
        help="Known-good exemplar guideline ID (repeatable)",
    )
    parser.add_argument(
        "--assume-built",
        action="store_true",
        help="Assume RF artifacts are already built and skip make.py invocation",
    )
    parser.add_argument(
        "--assume-built-reason",
        default="",
        help="Required operator reason when --assume-built is used",
    )
    return parser.parse_args()
