from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

from retrieval.core.profile_loader import (
    apply_profile_defaults,
    enforce_profile_corpus,
    load_retrieval_profile,
)
from retrieval.corpora.runtime_paths import resolve_corpus_runtime_paths
from retrieval.query.errors import ModeExecutionError
from retrieval.query.policy_resolution import resolve_row_projection_policy
from retrieval.query.review_artifacts import (
    build_review_artifact_payload,
    persist_review_artifact,
    resolve_review_artifact_path,
)
from semantic_backend_client import SemanticBackendConfig
from sqlite_query_guardrails import GuardrailError, execute_contract_query

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def run_query_main(
    *,
    args: Any,
    root: Path,
    parse_params: Callable[[str], dict[str, Any]],
    build_semantic_config: Callable[[Any], SemanticBackendConfig],
    execute_retrieval_query: Callable[..., dict[str, Any]],
    without_score_breakdown: Callable[[dict[str, Any]], dict[str, Any]],
    default_query_review_dir: str,
    review_artifact_schema_version: int,
) -> int:
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
            params = parse_params(args.params_json)
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

            semantic_config = build_semantic_config(args)
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
                result = without_score_breakdown(result)

        review_artifact_path = resolve_review_artifact_path(
            root=root,
            mode=str(args.mode),
            query_text=query_text,
            prompt_id=str(args.prompt_id),
            save_response_path=str(args.save_response_path),
            save_response_dir=str(args.save_response_dir),
            default_query_review_dir=default_query_review_dir,
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
                review_artifact_schema_version=review_artifact_schema_version,
            )
            persist_review_artifact(review_artifact_path, review_payload)
            print(f"[query-rust-reference][artifact] wrote {review_artifact_path}", file=sys.stderr)
    except ModeExecutionError as exc:
        print(f"[query-rust-reference][error][{exc.code}] {exc}")
        return EXIT_RUNTIME_FAIL
    except (json.JSONDecodeError, GuardrailError, OSError, sqlite3.Error) as exc:
        print(f"[query-rust-reference][error] {exc}")
        return EXIT_RUNTIME_FAIL

    print(json.dumps(result, indent=2, sort_keys=True))
    return EXIT_SUCCESS
