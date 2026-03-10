"""FLS paragraph lookup from the `fls_spec` SQLite knowledge base."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml

from context.fls_topology import (
    DEFAULT_TOPOLOGY_CACHE_PATH,
    get_paragraph,
    load_topology_index,
    paragraph_ids_for_document,
    paragraph_ids_for_section,
    topology_drift_report,
)
from context.fls_ws7 import resolve_guideline as resolve_ws7_guideline

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FLS_CANONICAL_DB_PATH = PROJECT_ROOT / ".cache" / "sqlite_kb" / "current" / "fls_spec.db"
FLS_COMPAT_DB_PATH = PROJECT_ROOT / "data" / "fls_spec.db"
FLS_DB_PATH = FLS_CANONICAL_DB_PATH
GUIDELINES_REPO_ROOT = Path(
    os.environ.get(
        "GUIDELINES_REPO", "/Users/pete.levasseur/personal/safety-critical-rust-coding-guidelines"
    )
)
SPEC_LOCK_PATH = GUIDELINES_REPO_ROOT / "src" / "spec.lock"
TOPOLOGY_PATH = DEFAULT_TOPOLOGY_CACHE_PATH
_TOPOLOGY_INDEX_CACHE: dict[str, Any] | None = None
_TOPOLOGY_DRIFT_CACHE: dict[str, Any] | None = None


def _bootstrap_scripts_path() -> None:
    scripts = PROJECT_ROOT / "scripts"
    value = str(scripts)
    if value not in sys.path:
        sys.path.insert(0, value)


def _load_fls_runtime() -> tuple[Path, Path, Path]:
    _bootstrap_scripts_path()
    from retrieval.corpora.config_loader import load_corpus_runtime_defaults

    defaults = load_corpus_runtime_defaults(root=PROJECT_ROOT, corpus="fls_spec")
    return defaults.db_path, defaults.contract_path, defaults.query_log_root


def _load_fls_runtime_settings() -> dict[str, Any]:
    _bootstrap_scripts_path()
    from retrieval.corpora.config_loader import load_corpus_runtime_defaults

    defaults = load_corpus_runtime_defaults(root=PROJECT_ROOT, corpus="fls_spec")
    corpus_cfg = (
        yaml.safe_load(
            (PROJECT_ROOT / "config" / "corpora" / "fls_spec.yaml").read_text(encoding="utf-8")
        )
        or {}
    )
    profile_path = PROJECT_ROOT / "config" / "retrieval_profiles" / f"{defaults.profile_name}.yaml"
    profile_cfg = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    runtime_cfg = corpus_cfg.get("runtime") or {}
    semantic_cfg = runtime_cfg.get("semantic") or {}
    retrieval_cfg = profile_cfg.get("retrieval") or {}
    models_cfg = profile_cfg.get("models") or {}
    hybrid_cfg = profile_cfg.get("hybrid") or {}
    runtime_retrieval_cfg = runtime_cfg.get("retrieval") or {}
    return {
        "db_path": defaults.db_path,
        "contract_path": defaults.contract_path,
        "query_log_root": defaults.query_log_root,
        "rewrite_rules_path": defaults.rewrite_rules_path,
        "top_k": int(retrieval_cfg.get("top_k", runtime_retrieval_cfg.get("top_k", 10))),
        "candidate_limit": int(
            retrieval_cfg.get("candidate_limit", runtime_retrieval_cfg.get("candidate_limit", 5000))
        ),
        "semantic_base_url": str(semantic_cfg.get("base_url", "http://127.0.0.1:8080")),
        "semantic_embed_base_url": str(
            semantic_cfg.get(
                "embed_base_url", semantic_cfg.get("base_url", "http://127.0.0.1:8080")
            )
        ),
        "semantic_rerank_base_url": str(
            semantic_cfg.get("rerank_base_url", "http://127.0.0.1:8081")
        ),
        "semantic_timeout_sec": float(semantic_cfg.get("timeout_sec", 120.0)),
        "semantic_retries": int(semantic_cfg.get("retries", 0)),
        "embed_model_id": str(models_cfg.get("embed_model_id", "Qwen/Qwen3-Embedding-4B")),
        "reranker_model_id": str(models_cfg.get("reranker_model_id", "BAAI/bge-reranker-v2-m3")),
        "hybrid_fusion_method": str(hybrid_cfg.get("fusion_method", "weighted-v1")),
        "hybrid_candidate_policy": str(hybrid_cfg.get("candidate_policy", "legacy")),
        "hybrid_rerank_pool_size": int(hybrid_cfg.get("rerank_pool_size", 0)),
        "hybrid_lexical_min": int(hybrid_cfg.get("lexical_min", 0)),
        "hybrid_semantic_min": int(hybrid_cfg.get("semantic_min", 0)),
        "hybrid_lexical_floor_count": int(hybrid_cfg.get("lexical_floor_count", 0)),
        "hybrid_lexical_floor_share": float(hybrid_cfg.get("lexical_floor_share", 0.0)),
        "hybrid_rrf_k": int(hybrid_cfg.get("rrf_k", 60)),
        "hybrid_rrf_window": int(hybrid_cfg.get("rrf_window", 0)),
    }


def _query_contract(
    *,
    query_id: str,
    params: dict[str, Any] | None = None,
    row_limit: int | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    _bootstrap_scripts_path()
    from sqlite_query_guardrails import execute_contract_query

    resolved_db_path = _resolve_fls_db_path(db_path)
    _, contract_path, query_log_root = _load_fls_runtime()
    return execute_contract_query(
        db_path=resolved_db_path,
        contract_path=contract_path,
        query_id=query_id,
        params=params,
        row_limit=row_limit,
        query_log_root=query_log_root,
    )


def _resolve_fls_db_path(db_path: Path | None = None) -> Path:
    if db_path is not None:
        return db_path
    if FLS_CANONICAL_DB_PATH.exists():
        return FLS_CANONICAL_DB_PATH
    return FLS_COMPAT_DB_PATH


def _load_live_topology_index(*, refresh: bool = False) -> dict[str, Any]:
    global _TOPOLOGY_INDEX_CACHE, _TOPOLOGY_DRIFT_CACHE
    if refresh or _TOPOLOGY_INDEX_CACHE is None:
        _TOPOLOGY_INDEX_CACHE = load_topology_index(topology_path=TOPOLOGY_PATH, refresh=refresh)
        db_path = _resolve_fls_db_path()
        if db_path.exists():
            _TOPOLOGY_DRIFT_CACHE = topology_drift_report(
                db_path=db_path,
                topology_index=_TOPOLOGY_INDEX_CACHE,
            )
    return _TOPOLOGY_INDEX_CACHE or {}


def get_live_topology_membership(*, paragraph_id: str) -> dict[str, Any] | None:
    topology_index = _load_live_topology_index()
    paragraph = get_paragraph(topology_index, paragraph_id)
    if paragraph is None:
        return None
    return {
        "paragraph_id": paragraph.paragraph_id,
        "paragraph_number": paragraph.number,
        "paragraph_link": paragraph.paragraph_link,
        "checksum": paragraph.checksum,
        "document_link": paragraph.document_link,
        "section_link": paragraph.section_link,
        "document_paragraph_ids": paragraph_ids_for_document(
            topology_index, paragraph.document_link
        ),
        "section_paragraph_ids": paragraph_ids_for_section(topology_index, paragraph.section_link),
    }


def _unresolved(reason: str, *, decision: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "paragraph_id": "fls_UNRESOLVED",
        "text": "",
        "chapter": "",
        "section": "",
        "paragraph_number": "",
        "unresolved_reason": reason,
    }
    if decision:
        payload["decision"] = decision
    return payload


def _db_has_paragraphs(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    try:
        result = _query_contract(query_id="fls_stats_v2", params={}, row_limit=1, db_path=db_path)
        rows = list(result.get("rows") or [])
        count = (
            int(rows[0].get("retrieval_eligible_count", rows[0].get("paragraph_count", 0)) or 0)
            if rows
            else 0
        )
    except Exception:
        return False
    return count > 0


def validate_fls_id(paragraph_id: str, *, spec_lock_path: Path = SPEC_LOCK_PATH) -> bool:
    del spec_lock_path
    topology_index = _load_live_topology_index()
    return get_paragraph(topology_index, paragraph_id) is not None


def resolve_fls_for_guideline(
    packet: dict[str, Any],
    *,
    db_path: Path | None = None,
    spec_lock_path: Path = SPEC_LOCK_PATH,
    precomputed_candidates: list[dict[str, Any]] | None = None,
    precomputed_variants: list[dict[str, str]] | None = None,
    policy_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del spec_lock_path, precomputed_candidates, precomputed_variants
    db_path = _resolve_fls_db_path(db_path)
    if not _db_has_paragraphs(db_path):
        raise RuntimeError(
            "FLS DB unavailable or empty; build "
            ".cache/sqlite_kb/current/fls_spec.db before FLS resolution."
        )

    construct_terms = [
        token.strip()
        for token in list(packet.get("construct_terms") or [])
        if token and str(token).strip()
    ]

    terms = [term.strip() for term in construct_terms if term.strip()]
    if not terms:
        return _unresolved("no construct terms provided")
    return resolve_ws7_guideline(
        project_root=PROJECT_ROOT,
        packet=packet,
        db_path=db_path,
        runtime_settings=_load_fls_runtime_settings(),
        topology_index=_load_live_topology_index(),
        policy_overrides=policy_overrides,
    )


def resolve_fls_for_construct(
    construct_terms: list[str],
    *,
    db_path: Path | None = None,
    spec_lock_path: Path = SPEC_LOCK_PATH,
    expected_domains: list[str] | None = None,
    policy_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del expected_domains
    packet = {
        "governing_obligation": " ".join(construct_terms),
        "construct_terms": list(construct_terms),
        "code_tokens": [],
        "supporting_phrases": [" ".join(construct_terms)] if construct_terms else [],
        "prior_documents": [],
        "prior_sections": [],
        "ambiguity_notes": [],
    }
    return resolve_fls_for_guideline(
        packet,
        db_path=db_path,
        spec_lock_path=spec_lock_path,
        policy_overrides=policy_overrides,
    )


def get_fls_db_stats(db_path: Path | None = None) -> dict[str, Any]:
    db_path = _resolve_fls_db_path(db_path)
    if not db_path.exists():
        return {"available": False, "source": "none"}
    result = _query_contract(query_id="fls_stats_v2", params={}, row_limit=1, db_path=db_path)
    rows = list(result.get("rows") or [])
    top = rows[0] if rows else {}
    paragraph_count = int(top.get("paragraph_count", 0) or 0)
    chapter_count = int(top.get("chapter_count", 0) or 0)
    document_count = int(top.get("document_count", 0) or 0)
    section_count = int(top.get("section_count", 0) or 0)
    commit_sha = str(top.get("commit_sha", "") or "")
    built_at = str(top.get("built_at", "") or "")

    stats: dict[str, Any] = {
        "available": paragraph_count > 0,
        "source": "fls_spec_db" if paragraph_count > 0 else "none",
        "paragraph_count": paragraph_count,
        "chapter_count": chapter_count,
        "document_count": document_count,
        "section_count": section_count,
    }
    if commit_sha:
        stats["commit_sha"] = commit_sha
    if built_at:
        stats["built_at"] = built_at
    return stats
