#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retrieval.core.policy_loader import load_eval_policy
from retrieval.core.profile_loader import (
    apply_profile_defaults,
    enforce_profile_corpus,
    load_retrieval_profile,
)
from retrieval.corpora.registry import get_corpus_adapter, list_supported_corpora
from retrieval.corpora.runtime_paths import resolve_corpus_runtime_paths
from retrieval.eval.prompt_routing import (
    HYBRID_FUSION_ROUTING_BEST_PRACTICE_V1,
    HYBRID_FUSION_ROUTING_OFF,
)
from retrieval.eval.reporting import (
    infer_root_cause_run_and_cell as eval_infer_root_cause_run_and_cell,
)
from retrieval.eval.reporting import write_eval_report
from retrieval.eval.runtime import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_TOP_K,
    SKEW_THRESHOLDS,
    THRESHOLDS,
    evaluate_retrieval_prompts as core_evaluate_retrieval_prompts,
    load_eval_prompts as core_load_eval_prompts,
)
from retrieval.eval.runtime_support import (
    load_yaml_mapping as _load_yaml,
    validate_required_evidence_fields as _validate_required_evidence_fields,
)
from retrieval.operations.query import execute_retrieval_query, resolve_row_projection_policy
from semantic_backend_client import (
    SemanticBackendConfig,
    check_semantic_backend,
    resolve_embed_base_url,
    resolve_rerank_base_url,
)
from sqlite_query_guardrails import GuardrailError

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def load_eval_prompts(path: Path) -> list[dict[str, Any]]:
    return core_load_eval_prompts(path)


def evaluate_retrieval_prompts(**kwargs: Any) -> dict[str, Any]:
    kwargs["execute_retrieval_fn"] = execute_retrieval_query
    return core_evaluate_retrieval_prompts(**kwargs)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate lexical/semantic/hybrid retrieval quality for rust_reference.sqlite"
    )
    parser.add_argument(
        "--corpus",
        choices=list_supported_corpora(),
        default="rust_reference",
        help="Corpus adapter used to resolve default DB/contract paths",
    )
    parser.add_argument(
        "--operation",
        choices=("eval", "verify"),
        default="eval",
        help="Operation tag included in eval report payload",
    )
    parser.add_argument("--db-path", default="", help="Path to corpus sqlite database")
    parser.add_argument("--contract-path", default="", help="Path to query contract file")
    parser.add_argument(
        "--eval-path",
        default="",
        help="Path to retrieval eval prompt definitions (defaults from --corpus)",
    )
    parser.add_argument(
        "--retrieval-profile-path",
        default="",
        help="Optional retrieval profile YAML for model/fusion defaults",
    )
    parser.add_argument("--query-log-root", default="", help="Directory for query audit logs")
    parser.add_argument(
        "--report-path", default=None, help="Optional output path for evaluation report"
    )
    parser.add_argument(
        "--backend-attempt-log-path",
        default=None,
        help="Optional JSONL path for semantic/rerank backend attempt traces",
    )
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--candidate-limit", type=int, default=DEFAULT_CANDIDATE_LIMIT)
    parser.add_argument(
        "--hybrid-fusion-method",
        choices=("weighted-v1", "weighted-v2", "rrf-v1"),
        default="weighted-v1",
    )
    parser.add_argument(
        "--hybrid-fusion-routing",
        choices=(HYBRID_FUSION_ROUTING_OFF, HYBRID_FUSION_ROUTING_BEST_PRACTICE_V1),
        default=HYBRID_FUSION_ROUTING_OFF,
        help="Optional routing policy that overrides hybrid fusion method per prompt family",
    )
    parser.add_argument("--hybrid-rrf-k", type=int, default=60)
    parser.add_argument(
        "--hybrid-rrf-window",
        type=int,
        default=0,
        help="Optional rank window for RRF (0 means auto)",
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
        "--hybrid-candidate-policy",
        choices=("legacy", "v2"),
        default="legacy",
        help="Hybrid candidate assembly policy before fusion",
    )
    parser.add_argument("--hybrid-rerank-pool-size", type=int, default=0)
    parser.add_argument("--hybrid-lexical-min", type=int, default=0)
    parser.add_argument("--hybrid-semantic-min", type=int, default=0)
    parser.add_argument("--allow-degraded", action="store_true")
    parser.add_argument("--semantic-base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--semantic-embed-base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--semantic-rerank-base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--embed-model-id", default="Qwen/Qwen3-Embedding-4B")
    parser.add_argument("--reranker-model-id", default="BAAI/bge-reranker-v2-m3")
    parser.add_argument("--semantic-timeout-sec", type=float, default=60.0)
    parser.add_argument("--semantic-retries", type=int, default=0)
    parser.add_argument(
        "--enforce-gates",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Apply threshold gates to aggregate retrieval metrics",
    )
    parser.add_argument(
        "--auto-start-local-backend",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Start local backend if semantic preflight is unavailable",
    )
    parser.add_argument("--keep-local-backend-running", action="store_true")
    parser.add_argument(
        "--local-embed-device", choices=("auto", "cpu", "mps", "cuda"), default="auto"
    )
    parser.add_argument(
        "--local-rerank-device", choices=("auto", "cpu", "mps", "cuda"), default="auto"
    )
    parser.add_argument("--local-model-cache-dir", default=".cache/sqlite_kb/models/hf")
    parser.add_argument("--local-startup-timeout-sec", type=float, default=180.0)
    parser.add_argument(
        "--allow-provenance-mismatch",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


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
        rewrite_rules_path="",
    )

    db_path = runtime_paths.db_path
    contract_path = runtime_paths.contract_path
    eval_path_raw = str(args.eval_path).strip() or str(
        get_corpus_adapter(corpus).config.default_eval_path
    )
    eval_path = (root / eval_path_raw).resolve()
    query_log_root = runtime_paths.query_log_root
    rewrite_rules_path = runtime_paths.rewrite_rules_path
    report_path = (
        (root / args.report_path).resolve()
        if args.report_path
        else runtime_paths.report_root
        / f"retrieval_eval_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    root_cause_run_id, root_cause_cell_id = eval_infer_root_cause_run_and_cell(report_path)

    thresholds = dict(THRESHOLDS)
    skew_thresholds = dict(SKEW_THRESHOLDS)
    policy_path = (root / str(get_corpus_adapter(corpus).config.default_eval_policy_path)).resolve()
    if policy_path.exists():
        policy = load_eval_policy(policy_path)
        loaded_thresholds = policy.get("thresholds")
        loaded_skew = policy.get("skew_thresholds")
        if isinstance(loaded_thresholds, dict):
            thresholds = loaded_thresholds
        if isinstance(loaded_skew, dict):
            skew_thresholds = {str(key): float(value) for key, value in loaded_skew.items()}

    backend_attempt_log_path = (
        (root / args.backend_attempt_log_path).resolve() if args.backend_attempt_log_path else None
    )
    row_projection_policy = resolve_row_projection_policy(root=root, corpus=corpus)
    if backend_attempt_log_path is not None:
        backend_attempt_log_path.parent.mkdir(parents=True, exist_ok=True)
        if backend_attempt_log_path.exists():
            backend_attempt_log_path.unlink()

    semantic_config = SemanticBackendConfig(
        base_url=str(args.semantic_base_url),
        embed_model_id=str(args.embed_model_id),
        reranker_model_id=str(args.reranker_model_id),
        timeout_sec=float(args.semantic_timeout_sec),
        embed_base_url=(str(args.semantic_embed_base_url).strip() or None),
        rerank_base_url=(str(args.semantic_rerank_base_url).strip() or None),
    )

    started_local_backend = False
    if bool(args.auto_start_local_backend):
        preflight = check_semantic_backend(semantic_config)
        if not bool(preflight.get("ok", False)):
            start_command = [
                sys.executable,
                str(root / "scripts/sqlite_local_semantic_backend.py"),
                "start",
                "--embed-base-url",
                resolve_embed_base_url(semantic_config),
                "--rerank-base-url",
                resolve_rerank_base_url(semantic_config),
                "--embed-model-id",
                str(args.embed_model_id),
                "--rerank-model-id",
                str(args.reranker_model_id),
                "--embed-device",
                str(args.local_embed_device),
                "--rerank-device",
                str(args.local_rerank_device),
                "--model-cache-dir",
                str(args.local_model_cache_dir),
                "--startup-timeout-sec",
                str(args.local_startup_timeout_sec),
            ]
            completed = subprocess.run(start_command, check=False)
            if completed.returncode != 0:
                print(
                    "[eval-rust-reference-retrieval][error] "
                    "failed to auto-start local semantic backend"
                )
                return EXIT_RUNTIME_FAIL
            started_local_backend = True

    try:
        prompts = load_eval_prompts(eval_path)
        eval_payload = _load_yaml(eval_path)
        suite_id = str(eval_payload.get("suite_id", "")).strip() or eval_path.stem
        if suite_id.startswith("core_docs_"):
            _validate_required_evidence_fields(db_path, prompts)
        report = evaluate_retrieval_prompts(
            db_path=db_path,
            contract_path=contract_path,
            query_log_root=query_log_root,
            prompts=prompts,
            top_k=int(args.top_k),
            candidate_limit=int(args.candidate_limit),
            allow_degraded=bool(args.allow_degraded),
            semantic_config=semantic_config,
            semantic_retries=int(args.semantic_retries),
            enforce_gates=bool(args.enforce_gates),
            model_cache_dir=str(args.local_model_cache_dir),
            backend_profile="python-local",
            backend_attempt_log_path=backend_attempt_log_path,
            root_cause_run_id=root_cause_run_id,
            root_cause_cell_id=root_cause_cell_id,
            hybrid_fusion_method=str(args.hybrid_fusion_method),
            hybrid_rrf_k=int(args.hybrid_rrf_k),
            hybrid_rrf_window=int(args.hybrid_rrf_window),
            hybrid_fusion_routing=str(args.hybrid_fusion_routing),
            hybrid_lexical_floor_count=int(args.hybrid_lexical_floor_count),
            hybrid_lexical_floor_share=float(args.hybrid_lexical_floor_share),
            hybrid_candidate_policy=str(args.hybrid_candidate_policy),
            hybrid_rerank_pool_size=int(args.hybrid_rerank_pool_size),
            hybrid_lexical_min=int(args.hybrid_lexical_min),
            hybrid_semantic_min=int(args.hybrid_semantic_min),
            suite_id=suite_id,
            operation=str(args.operation),
            row_projection_policy=row_projection_policy,
            corpus=corpus,
            rewrite_mode="auto",
            rewrite_rules_path=rewrite_rules_path,
            thresholds=thresholds,
            skew_thresholds=skew_thresholds,
        )
    except (RuntimeError, GuardrailError, OSError) as exc:
        print(f"[eval-rust-reference-retrieval][error] {exc}")
        return EXIT_RUNTIME_FAIL
    finally:
        if started_local_backend and not bool(args.keep_local_backend_running):
            subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts/sqlite_local_semantic_backend.py"),
                    "stop",
                ],
                check=False,
            )

    write_eval_report(report_path, report)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"[eval-rust-reference-retrieval] report -> {report_path}")
    if int(report["summary"]["failed_cases"]) > 0:
        print("[eval-rust-reference-retrieval][error] Retrieval evaluation failures detected")
        return EXIT_RUNTIME_FAIL
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
