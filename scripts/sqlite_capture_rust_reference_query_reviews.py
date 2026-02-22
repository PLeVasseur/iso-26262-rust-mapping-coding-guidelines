#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from semantic_backend_client import SemanticBackendConfig
from sqlite_query_guardrails import GuardrailError
from sqlite_query_rust_reference import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_QUERY_REVIEW_DIR,
    DEFAULT_TOP_K,
    ModeExecutionError,
    build_review_artifact_payload,
    execute_retrieval_query,
    persist_review_artifact,
)

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3
VALID_MODES = {"lexical", "semantic", "hybrid"}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _split_csv(raw: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for token in [part.strip().lower() for part in str(raw).split(",") if part.strip()]:
        if token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _safe_slug(value: str, fallback: str) -> str:
    import re

    tokens = [token for token in re.findall(r"[a-z0-9]+", str(value).lower()) if token]
    if not tokens:
        return fallback
    joined = "-".join(tokens)
    clipped = joined[:80].strip("-")
    return clipped if clipped else fallback


def _load_prompt_pack(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    raw_prompts: Any
    if isinstance(payload, dict):
        raw_prompts = payload.get("prompts", [])
    else:
        raw_prompts = payload

    if not isinstance(raw_prompts, list) or not raw_prompts:
        raise GuardrailError("Prompt pack must contain non-empty prompts list")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, raw in enumerate(raw_prompts, start=1):
        if not isinstance(raw, dict):
            raise GuardrailError(f"Prompt entry #{idx} must be an object")

        query_text = str(raw.get("query_text", "")).strip()
        if not query_text:
            raise GuardrailError(f"Prompt entry #{idx} missing query_text")

        prompt_id = str(raw.get("prompt_id", "")).strip() or f"prompt-{idx:03d}"
        if prompt_id in seen_ids:
            raise GuardrailError(f"Duplicate prompt_id: {prompt_id}")
        seen_ids.add(prompt_id)

        modes_raw = raw.get("modes")
        modes = [str(mode).strip().lower() for mode in modes_raw] if isinstance(modes_raw, list) else []
        modes = [mode for mode in modes if mode in VALID_MODES]

        normalized.append(
            {
                "prompt_id": prompt_id,
                "query_text": query_text,
                "row_marker": str(raw.get("row_marker", "")).strip().lower(),
                "modes": modes,
            }
        )
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture persisted query review artifacts for prompt packs"
    )
    parser.add_argument(
        "--prompts-path",
        default="data/query_testsets/rust_reference_table1_retrieval_eval.yaml",
        help="YAML/JSON prompt pack path",
    )
    parser.add_argument("--prompt-ids", default="", help="Optional CSV filter for prompt IDs")
    parser.add_argument(
        "--modes",
        default="",
        help="Optional CSV mode override (lexical,semantic,hybrid)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional cap on number of prompts processed (0 means all)",
    )
    parser.add_argument(
        "--bundle-id",
        default="",
        help="Optional deterministic bundle directory name under output dir",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_QUERY_REVIEW_DIR,
        help="Directory root for query review bundles",
    )
    parser.add_argument(
        "--db-path",
        default=".cache/sqlite_kb/current/rust_reference.sqlite",
        help="Path to rust_reference.sqlite",
    )
    parser.add_argument(
        "--contract-path",
        default="config/sqlite_query_contracts/rust_reference.yaml",
        help="Path to rust_reference query contract YAML",
    )
    parser.add_argument(
        "--query-log-root",
        default=".cache/sqlite_kb/query_logs/rust_reference",
        help="Directory used for query audit logs",
    )
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--candidate-limit", type=int, default=DEFAULT_CANDIDATE_LIMIT)
    parser.add_argument(
        "--include-score-breakdown",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--allow-degraded", action="store_true")
    parser.add_argument(
        "--semantic-base-url",
        default=os.environ.get("RUST_REF_TEI_BASE_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument(
        "--semantic-embed-base-url",
        default=os.environ.get("RUST_REF_TEI_EMBED_BASE_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument(
        "--semantic-rerank-base-url",
        default=os.environ.get("RUST_REF_TEI_RERANK_BASE_URL", "http://127.0.0.1:8081"),
    )
    parser.add_argument(
        "--embed-model-id",
        default=os.environ.get("RUST_REF_EMBED_MODEL_ID", "Qwen/Qwen3-Embedding-4B"),
    )
    parser.add_argument(
        "--reranker-model-id",
        default=os.environ.get("RUST_REF_RERANK_MODEL_ID", "BAAI/bge-reranker-v2-m3"),
    )
    parser.add_argument(
        "--semantic-timeout-sec",
        type=float,
        default=float(os.environ.get("RUST_REF_SEMANTIC_TIMEOUT_SEC", "60.0")),
    )
    parser.add_argument("--semantic-retries", type=int, default=2)
    parser.add_argument(
        "--persist-semantic-cache",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--allow-online-corpus-embedding",
        action=argparse.BooleanOptionalAction,
        default=bool(int(os.environ.get("RUST_REF_ALLOW_ONLINE_CORPUS_EMBEDDING", "0"))),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]

    prompts_path = (root / str(args.prompts_path)).resolve()
    db_path = (root / str(args.db_path)).resolve()
    contract_path = (root / str(args.contract_path)).resolve()
    query_log_root = (root / str(args.query_log_root)).resolve()

    if not prompts_path.is_file():
        print(f"[capture-query-reviews][error] Prompt pack not found: {prompts_path}")
        return EXIT_RUNTIME_FAIL

    prompt_id_filter = set(_split_csv(str(args.prompt_ids)))
    mode_override = _split_csv(str(args.modes))
    if any(mode not in VALID_MODES for mode in mode_override):
        print("[capture-query-reviews][error] --modes must be lexical,semantic,hybrid")
        return EXIT_RUNTIME_FAIL

    semantic_config = SemanticBackendConfig(
        base_url=str(args.semantic_base_url),
        embed_model_id=str(args.embed_model_id),
        reranker_model_id=str(args.reranker_model_id),
        timeout_sec=float(args.semantic_timeout_sec),
        embed_base_url=(str(args.semantic_embed_base_url).strip() or None),
        rerank_base_url=(str(args.semantic_rerank_base_url).strip() or None),
    )
    allow_degraded = bool(args.allow_degraded)
    if not allow_degraded:
        allow_degraded = os.environ.get("RUST_REF_ALLOW_DEGRADED", "0") == "1"

    try:
        prompts = _load_prompt_pack(prompts_path)
    except (GuardrailError, OSError, RuntimeError) as exc:
        print(f"[capture-query-reviews][error] {exc}")
        return EXIT_RUNTIME_FAIL

    selected_prompts: list[dict[str, Any]] = []
    for prompt in prompts:
        prompt_id = str(prompt["prompt_id"]).strip().lower()
        if prompt_id_filter and prompt_id not in prompt_id_filter:
            continue
        selected_prompts.append(prompt)

    if int(args.limit) > 0:
        selected_prompts = selected_prompts[: int(args.limit)]

    if not selected_prompts:
        print("[capture-query-reviews][error] No prompts selected")
        return EXIT_RUNTIME_FAIL

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    bundle_id = str(args.bundle_id).strip() or stamp
    output_root = (root / str(args.output_dir)).resolve()
    bundle_dir = (output_root / bundle_id).resolve()
    bundle_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    wrote_count = 0
    failure_count = 0

    for prompt in selected_prompts:
        prompt_id = str(prompt["prompt_id"]).strip()
        query_text = str(prompt["query_text"]).strip()
        row_marker = str(prompt.get("row_marker", "")).strip().lower()
        prompt_modes = mode_override or list(prompt.get("modes", [])) or ["hybrid"]
        prompt_modes = [mode for mode in prompt_modes if mode in VALID_MODES]
        if not prompt_modes:
            prompt_modes = ["hybrid"]

        for mode in prompt_modes:
            filename = f"{stamp}__{_safe_slug(prompt_id, 'prompt')}__{mode}.json"
            artifact_path = bundle_dir / filename

            entry: dict[str, Any] = {
                "prompt_id": prompt_id,
                "mode": mode,
                "artifact_path": str(artifact_path),
                "status": "pass",
                "error_code": "",
                "error_message": "",
            }
            try:
                response = execute_retrieval_query(
                    mode=mode,
                    db_path=db_path,
                    contract_path=contract_path,
                    query_log_root=query_log_root,
                    query_text=query_text,
                    row_marker=row_marker,
                    top_k=int(args.top_k),
                    candidate_limit=int(args.candidate_limit),
                    allow_degraded=allow_degraded,
                    semantic_config=semantic_config,
                    semantic_retries=int(args.semantic_retries),
                    persist_semantic_cache=bool(args.persist_semantic_cache),
                    allow_online_corpus_embedding=bool(args.allow_online_corpus_embedding),
                )
                if not bool(args.include_score_breakdown):
                    response = dict(response)
                    response.pop("row_projection", None)
                    rows = []
                    for row in response.get("rows", []):
                        if not isinstance(row, dict):
                            continue
                        rows.append(
                            {
                                key: value
                                for key, value in row.items()
                                if key
                                not in {
                                    "bm25_raw",
                                    "phrase_match",
                                    "token_overlap_count",
                                    "lexical_score",
                                    "semantic_score",
                                    "semantic_score_raw",
                                    "reranker_score",
                                    "relevance_score",
                                    "row_marker_scores",
                                }
                            }
                        )
                    response["rows"] = rows

            except ModeExecutionError as exc:
                response = {
                    "requested_mode": mode,
                    "executed_mode": "",
                    "degraded": False,
                    "error": str(exc),
                    "error_code": str(exc.code),
                    "query_text": query_text,
                    "row_marker": row_marker,
                    "rows": [],
                }
                entry["status"] = "fail"
                entry["error_code"] = str(exc.code)
                entry["error_message"] = str(exc)
                failure_count += 1
            except (GuardrailError, OSError, RuntimeError, ValueError) as exc:
                response = {
                    "requested_mode": mode,
                    "executed_mode": "",
                    "degraded": False,
                    "error": str(exc),
                    "error_code": "RUNTIME_FAIL",
                    "query_text": query_text,
                    "row_marker": row_marker,
                    "rows": [],
                }
                entry["status"] = "fail"
                entry["error_code"] = "RUNTIME_FAIL"
                entry["error_message"] = str(exc)
                failure_count += 1

            payload = build_review_artifact_payload(
                mode=mode,
                query_text=query_text,
                row_marker=row_marker,
                prompt_id=prompt_id,
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
                response=response,
            )
            persist_review_artifact(artifact_path, payload)
            wrote_count += 1
            entries.append(entry)

    manifest = {
        "schema_version": 1,
        "generated_at": _utc_now(),
        "bundle_id": bundle_id,
        "bundle_dir": str(bundle_dir),
        "prompts_path": str(prompts_path),
        "mode_override": mode_override,
        "top_k": int(args.top_k),
        "candidate_limit": int(args.candidate_limit),
        "include_score_breakdown": bool(args.include_score_breakdown),
        "allow_degraded": bool(allow_degraded),
        "semantic_config": {
            "base_url": str(semantic_config.base_url),
            "embed_base_url": str(semantic_config.embed_base_url or semantic_config.base_url),
            "rerank_base_url": str(semantic_config.rerank_base_url or semantic_config.base_url),
            "embed_model_id": str(semantic_config.embed_model_id),
            "reranker_model_id": str(semantic_config.reranker_model_id),
            "timeout_sec": float(semantic_config.timeout_sec),
            "semantic_retries": int(args.semantic_retries),
            "persist_semantic_cache": bool(args.persist_semantic_cache),
            "allow_online_corpus_embedding": bool(args.allow_online_corpus_embedding),
        },
        "summary": {
            "selected_prompt_count": len(selected_prompts),
            "written_artifact_count": wrote_count,
            "failed_case_count": failure_count,
        },
        "entries": entries,
    }

    manifest_path = bundle_dir / "manifest.json"
    persist_review_artifact(manifest_path, manifest)

    if wrote_count <= 0:
        print("[capture-query-reviews][error] No review artifacts written")
        return EXIT_RUNTIME_FAIL

    print(json.dumps(manifest["summary"], indent=2, sort_keys=True))
    print(f"[capture-query-reviews] manifest: {manifest_path}")
    if failure_count > 0:
        return EXIT_RUNTIME_FAIL
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
