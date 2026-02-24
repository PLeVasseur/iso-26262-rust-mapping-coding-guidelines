from __future__ import annotations

import json
from pathlib import Path

from retrieval.core.provenance import (
    assert_schema_up_to_date,
    build_pipeline_fingerprint_payload,
    canonical_json_hash,
    read_latest_pipeline_run,
)
from retrieval.corpora.registry import get_corpus_adapter


def _get_flag_value(args: list[str], flag: str) -> str:
    for idx, token in enumerate(args):
        if token == flag and idx + 1 < len(args):
            return str(args[idx + 1]).strip()
    return ""


def _has_flag(args: list[str], flag: str) -> bool:
    return flag in args


def _resolve_path(root: Path, raw: str, fallback: Path) -> Path:
    candidate = str(raw).strip()
    if not candidate:
        return fallback
    path = Path(candidate)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def enforce_provenance_guard(
    *,
    root: Path,
    operation: str,
    corpus: str,
    default_db_path: Path,
    default_profile_name: str,
    default_eval_policy_id: str,
    default_ingest_strategy: str,
    chunk_target_min_tokens: int,
    chunk_target_max_tokens: int,
    extra_args: list[str],
) -> None:
    if str(operation) == "migrate":
        return

    db_path = _resolve_path(root, _get_flag_value(extra_args, "--db-path"), default_db_path)
    if not db_path.exists():
        if str(operation) == "build":
            return
        raise RuntimeError(f"Database not found for provenance check: {db_path}")

    latest_migration_id = assert_schema_up_to_date(db_path, root=root)

    allow_mismatch = _has_flag(extra_args, "--allow-provenance-mismatch")
    source_state = get_corpus_adapter(corpus).compute_source_state(db_path)

    profile_path = _get_flag_value(extra_args, "--retrieval-profile-path")
    if profile_path:
        profile_id = Path(profile_path).stem
    else:
        profile_id = str(default_profile_name)

    eval_policy_id = str(default_eval_policy_id)
    ingest_strategy = _get_flag_value(extra_args, "--ingest-strategy") or str(
        default_ingest_strategy
    )

    min_override = _get_flag_value(extra_args, "--chunk-target-min-tokens")
    max_override = _get_flag_value(extra_args, "--chunk-target-max-tokens")
    min_tokens = int(min_override) if min_override else int(chunk_target_min_tokens)
    max_tokens = int(max_override) if max_override else int(chunk_target_max_tokens)

    embed_model = _get_flag_value(extra_args, "--embed-model-id") or "Qwen/Qwen3-Embedding-4B"
    reranker_model = _get_flag_value(extra_args, "--reranker-model-id") or "BAAI/bge-reranker-v2-m3"
    embedding_dim = _get_flag_value(extra_args, "--embedding-dim") or "0"
    model_fingerprint = canonical_json_hash(
        {
            "embed_model_id": embed_model,
            "reranker_model_id": reranker_model,
            "embedding_dim": int(embedding_dim),
        }
    )

    payload = build_pipeline_fingerprint_payload(
        corpus=corpus,
        source_state=source_state,
        schema_migration_id=latest_migration_id,
        ingest_strategy=ingest_strategy,
        ingest_strategy_version="1",
        ingest_params={"target_min_tokens": min_tokens, "target_max_tokens": max_tokens},
        retrieval_profile_id=profile_id,
        eval_policy_id=eval_policy_id,
        model_fingerprint=model_fingerprint,
    )
    expected_fingerprint = canonical_json_hash(payload)

    latest_run = read_latest_pipeline_run(db_path, corpus=corpus)
    if latest_run is None:
        raise RuntimeError(
            "Database missing pipeline provenance metadata. "
            "Run 'sqlite_kb.py migrate --corpus ...' once to initialize provenance state."
        )

    actual_fingerprint = str(latest_run.get("pipeline_fingerprint", "")).strip()
    if expected_fingerprint == actual_fingerprint:
        return

    mismatch_payload = {
        "operation": operation,
        "corpus": corpus,
        "expected_fingerprint": expected_fingerprint,
        "actual_fingerprint": actual_fingerprint,
        "expected": payload,
        "actual": latest_run,
    }
    if allow_mismatch:
        print(
            json.dumps(
                {
                    "status": "provenance_mismatch_override",
                    "payload": mismatch_payload,
                },
                sort_keys=True,
            )
        )
        return

    raise RuntimeError(
        "Provenance fingerprint mismatch (hard-fail). "
        f"Diff: {json.dumps(mismatch_payload, sort_keys=True)}"
    )
