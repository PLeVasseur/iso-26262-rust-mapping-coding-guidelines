"""FLS paragraph lookup from the `fls_spec` SQLite knowledge base."""

from __future__ import annotations

import json
import math
import os
import re
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
EXEMPLAR_MANIFEST = PROJECT_ROOT / "data" / "exemplar_manifest.json"
_EXEMPLAR_OVERRIDES: list[dict[str, Any]] | None = None
TOPOLOGY_PATH = DEFAULT_TOPOLOGY_CACHE_PATH
_TOPOLOGY_INDEX_CACHE: dict[str, Any] | None = None
_TOPOLOGY_DRIFT_CACHE: dict[str, Any] | None = None

POLICY_PATH = PROJECT_ROOT / "config" / "fls_resolution_policy.yaml"
_POLICY_CACHE: dict[str, Any] | None = None

MIN_CONFIDENCE_SCORE = 0.52
MIN_CONFIDENCE_MARGIN = 0.06
MIN_TERM_OVERLAP = 0.18
MIN_TERM_HITS = 2
MIN_VARIANT_COVERAGE = 2

DEFAULT_POLICY: dict[str, Any] = {
    "thresholds": {
        "min_confidence_score": MIN_CONFIDENCE_SCORE,
        "min_confidence_margin": MIN_CONFIDENCE_MARGIN,
        "min_weighted_overlap": 0.14,
        "min_term_hits": MIN_TERM_HITS,
        "min_variant_coverage": MIN_VARIANT_COVERAGE,
        "min_high_trust_field_match": 1,
        "min_high_trust_field_overlap": 0.08,
    },
    "review_thresholds": {
        "min_confidence_score": 0.38,
        "min_weighted_overlap": 0.10,
        "min_variant_coverage": 1,
        "min_high_trust_field_match": 1,
    },
    "weights": {
        "lexical": 0.20,
        "weighted_overlap": 0.40,
        "variant_coverage": 0.15,
        "code_overlap": 0.15,
        "chapter_bonus": 0.10,
    },
    "field_weights": {
        "title": 1.0,
        "claim": 1.0,
        "rationale": 0.75,
        "amplification": 0.65,
        "non_compliant_narrative": 0.55,
        "compliant_narrative": 0.45,
        "construct_terms": 0.70,
    },
    "field_top_k_terms": {
        "title": 12,
        "claim": 16,
        "rationale": 20,
        "amplification": 20,
        "non_compliant_narrative": 16,
        "compliant_narrative": 16,
        "construct_terms": 12,
    },
    "high_trust_fields": ["title", "claim", "construct_terms"],
}

DOMAIN_TO_CHAPTERS: dict[str, set[str]] = {
    "unsafe": {"Unsafety"},
    "defect": {"Unsafety"},
    "concurrency": {"Concurrency"},
    "implementations": {"Implementations"},
    "expressions": {"Expressions"},
}

DOMAIN_HINTS: dict[str, str] = {
    "unsafe": "unsafe",
    "undefined": "unsafe",
    "ub": "unsafe",
    "pointer": "unsafe",
    "dereference": "unsafe",
    "concurrency": "concurrency",
    "atomic": "concurrency",
    "thread": "concurrency",
    "send": "concurrency",
    "sync": "concurrency",
    "impl": "implementations",
    "implementation": "implementations",
    "trait": "implementations",
    "closure": "expressions",
}


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


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(dict(out[key]), value)
        else:
            out[key] = value
    return out


def _load_policy() -> dict[str, Any]:
    global _POLICY_CACHE
    if _POLICY_CACHE is not None:
        return _POLICY_CACHE
    policy = dict(DEFAULT_POLICY)
    if POLICY_PATH.exists():
        raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict):
            policy = _deep_merge(policy, raw)
    _POLICY_CACHE = policy
    return _POLICY_CACHE


def _effective_policy(policy_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    base = _load_policy()
    if not policy_overrides:
        return base
    return _deep_merge(dict(base), policy_overrides)


def _policy_threshold(
    payload: dict[str, Any],
    *,
    name: str,
    default: float,
    section: str = "thresholds",
) -> float:
    thresholds_raw = payload.get(section)
    thresholds: dict[str, Any] = thresholds_raw if isinstance(thresholds_raw, dict) else {}
    value = thresholds.get(name, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


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
        "paragraph_link": paragraph.paragraph_link,
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


def _tokenize_text(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9_]+", text) if len(token) >= 3}


def _tokenize_code(text: str) -> set[str]:
    return {
        token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text) if len(token) >= 3
    }


def _normalize_candidate_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "paragraph_id": str(row.get("paragraph_id") or row.get("statement_id") or "").strip(),
        "paragraph_number": str(row.get("paragraph_number", "")),
        "chapter": str(row.get("chapter", "")),
        "section": str(row.get("section", "")),
        "text": str(row.get("text") or row.get("statement_text") or ""),
        "source_file": str(row.get("source_file", "")),
        "bm25_rank": float(row.get("bm25_rank", row.get("bm25_raw", 0.0)) or 0.0),
        "lexical_score": float(row.get("lexical_score", 0.0) or 0.0),
        "variant_name": str(row.get("variant_name", "")).strip(),
    }


def _aggregate_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = _normalize_candidate_row(raw)
        paragraph_id = str(row.get("paragraph_id", "")).strip()
        if not paragraph_id:
            continue
        item = merged.get(paragraph_id)
        if item is None:
            item = {
                **row,
                "variant_names": [],
                "variant_count": 0,
                "max_lexical_score": float(row.get("lexical_score", 0.0) or 0.0),
            }
            merged[paragraph_id] = item
        else:
            item["max_lexical_score"] = max(
                float(item.get("max_lexical_score", 0.0) or 0.0),
                float(row.get("lexical_score", 0.0) or 0.0),
            )
            if not str(item.get("text", "")).strip() and str(row.get("text", "")).strip():
                item["text"] = str(row.get("text", ""))
            if not str(item.get("chapter", "")).strip() and str(row.get("chapter", "")).strip():
                item["chapter"] = str(row.get("chapter", ""))
            if (
                not str(item.get("paragraph_number", "")).strip()
                and str(row.get("paragraph_number", "")).strip()
            ):
                item["paragraph_number"] = str(row.get("paragraph_number", ""))
        variant_name = str(row.get("variant_name", "")).strip()
        if variant_name and variant_name not in item["variant_names"]:
            item["variant_names"].append(variant_name)
            item["variant_count"] = len(item["variant_names"])
    return list(merged.values())


def _idf_weights(candidates: list[dict[str, Any]]) -> dict[str, float]:
    docs: list[set[str]] = []
    for row in candidates:
        docs.append(_tokenize_text(str(row.get("text", ""))))
    n_docs = max(1, len(docs))
    df: dict[str, int] = {}
    for tokens in docs:
        for token in tokens:
            df[token] = int(df.get(token, 0)) + 1
    idf: dict[str, float] = {}
    for token, count in df.items():
        idf[token] = 1.0 + float(math.log((n_docs + 1.0) / (count + 1.0)))
    return idf


def _field_terms(packet: dict[str, Any], *, construct_terms: list[str]) -> dict[str, list[str]]:
    provided_raw = packet.get("field_terms")
    provided: dict[str, Any] = provided_raw if isinstance(provided_raw, dict) else {}
    out: dict[str, list[str]] = {}
    for key, value in provided.items():
        if not isinstance(value, list):
            continue
        out[str(key)] = [str(token).strip().lower() for token in value if str(token).strip()]
    if out:
        return out
    return {
        "title": list(_tokenize_text(str(packet.get("title", "")))),
        "claim": list(_tokenize_text(" ".join(list(packet.get("claim_phrases") or [])))),
        "rationale": list(_tokenize_text(str(packet.get("rationale_text", "")))),
        "amplification": list(_tokenize_text(str(packet.get("amplification_text", "")))),
        "non_compliant_narrative": list(
            _tokenize_text(str(packet.get("non_compliant_narrative", "")))
        ),
        "compliant_narrative": list(_tokenize_text(str(packet.get("compliant_narrative", "")))),
        "construct_terms": list(_tokenize_text(" ".join(construct_terms))),
    }


def _weighted_overlap_ratio(
    *, tokens: list[str], paragraph_tokens: set[str], idf: dict[str, float], top_k: int
) -> tuple[float, int]:
    ordered = sorted(
        {token for token in tokens if token},
        key=lambda token: (-float(idf.get(token, 1.0)), token),
    )
    selected = ordered[: max(1, int(top_k))]
    if not selected:
        return 0.0, 0
    denom = sum(float(idf.get(token, 1.0)) for token in selected)
    if denom <= 0.0:
        return 0.0, 0
    hits = [token for token in selected if token in paragraph_tokens]
    numer = sum(float(idf.get(token, 1.0)) for token in hits)
    return numer / denom, len(hits)


def _resolve_expected_domains(
    *, construct_terms: list[str], expected_domains: list[str] | None
) -> set[str]:
    resolved: set[str] = set()
    for raw in list(expected_domains or []):
        value = str(raw).strip().lower()
        if value in DOMAIN_TO_CHAPTERS:
            resolved.add(value)
    if resolved:
        return resolved

    hints = _tokenize_text(" ".join(construct_terms))
    for hint in hints:
        mapped = DOMAIN_HINTS.get(hint)
        if mapped:
            resolved.add(mapped)
    return resolved


def _candidate_chapter_match(*, chapter: str, domains: set[str]) -> bool:
    if not domains:
        return True
    chapter_name = str(chapter).strip()
    if not chapter_name:
        return False
    allowed: set[str] = set()
    for domain in domains:
        allowed.update(DOMAIN_TO_CHAPTERS.get(domain, set()))
    if not allowed:
        return True
    return chapter_name in allowed


def _score_candidates(
    *,
    candidates: list[dict[str, Any]],
    code_tokens: set[str],
    field_terms: dict[str, list[str]],
    domains: set[str],
    variant_count: int,
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    score_weights_raw = policy.get("weights")
    score_weights: dict[str, Any] = score_weights_raw if isinstance(score_weights_raw, dict) else {}
    field_weights_raw = policy.get("field_weights")
    field_weights: dict[str, Any] = field_weights_raw if isinstance(field_weights_raw, dict) else {}
    field_top_k_raw = policy.get("field_top_k_terms")
    field_top_k: dict[str, Any] = field_top_k_raw if isinstance(field_top_k_raw, dict) else {}
    high_trust_fields = [
        str(value) for value in list(policy.get("high_trust_fields") or []) if str(value).strip()
    ]

    lexical_values = [float(row.get("max_lexical_score", 0.0) or 0.0) for row in candidates]
    lexical_min = min(lexical_values) if lexical_values else 0.0
    lexical_max = max(lexical_values) if lexical_values else 0.0
    idf = _idf_weights(candidates)
    scored: list[dict[str, Any]] = []
    for row in candidates:
        lexical_raw = float(row.get("max_lexical_score", 0.0) or 0.0)
        if lexical_max > lexical_min:
            lexical_norm = (lexical_raw - lexical_min) / (lexical_max - lexical_min)
        else:
            lexical_norm = 1.0 if lexical_raw > 0.0 else 0.0
        paragraph_tokens = _tokenize_text(str(row.get("text", "")))

        weighted_overlap_total = 0.0
        active_field_weight = 0.0
        overlap_hits = 0
        high_trust_hits = 0
        per_field_overlap: dict[str, float] = {}
        for field_name, tokens in field_terms.items():
            if not tokens:
                continue
            weight = float(field_weights.get(field_name, 0.0) or 0.0)
            if weight <= 0.0:
                continue
            top_k = int(field_top_k.get(field_name, 12) or 12)
            ratio, hits = _weighted_overlap_ratio(
                tokens=tokens,
                paragraph_tokens=paragraph_tokens,
                idf=idf,
                top_k=top_k,
            )
            per_field_overlap[field_name] = round(ratio, 6)
            weighted_overlap_total += weight * ratio
            active_field_weight += weight
            overlap_hits += int(hits)
            if field_name in high_trust_fields and ratio >= _policy_threshold(
                policy,
                name="min_high_trust_field_overlap",
                default=0.08,
            ):
                high_trust_hits += 1
        overlap_ratio = (
            weighted_overlap_total / active_field_weight if active_field_weight > 0.0 else 0.0
        )

        code_hits = len(code_tokens & paragraph_tokens)
        code_ratio = float(code_hits) / float(len(code_tokens)) if code_tokens else 0.0
        candidate_variant_count = int(row.get("variant_count", 0) or 0)
        variant_coverage = (
            float(candidate_variant_count) / float(variant_count) if variant_count > 0 else 0.0
        )
        chapter = str(row.get("chapter", ""))
        chapter_match = _candidate_chapter_match(chapter=chapter, domains=domains)
        confidence = (
            (float(score_weights.get("weighted_overlap", 0.40) or 0.40) * overlap_ratio)
            + (float(score_weights.get("lexical", 0.20) or 0.20) * lexical_norm)
            + (float(score_weights.get("variant_coverage", 0.15) or 0.15) * variant_coverage)
            + (float(score_weights.get("code_overlap", 0.15) or 0.15) * code_ratio)
            + (float(score_weights.get("chapter_bonus", 0.10) or 0.10) if chapter_match else 0.0)
        )
        scored.append(
            {
                **row,
                "overlap_hits": overlap_hits,
                "overlap_ratio": round(overlap_ratio, 6),
                "weighted_overlap": round(overlap_ratio, 6),
                "per_field_overlap": per_field_overlap,
                "high_trust_field_hits": high_trust_hits,
                "code_overlap_hits": code_hits,
                "code_overlap_ratio": round(code_ratio, 6),
                "variant_coverage": round(variant_coverage, 6),
                "lexical_score_raw": lexical_raw,
                "lexical_score_norm": round(lexical_norm, 6),
                "chapter_match": chapter_match,
                "confidence": round(confidence, 6),
            }
        )
    scored.sort(
        key=lambda row: (
            -float(row.get("confidence", 0.0)),
            -float(row.get("variant_coverage", 0.0)),
            -int(row.get("overlap_hits", 0)),
            -float(row.get("lexical_score_raw", 0.0)),
            str(row.get("paragraph_id", "")),
        )
    )
    return scored


def _fts_query(text: str) -> str:
    if " AND " in text or " OR " in text or '"' in text:
        return text.strip()
    terms = re.findall(r"[A-Za-z0-9_]+", text)
    if not terms:
        return ""
    return " OR ".join(f'"{term}"' for term in terms[:8])


def _db_has_paragraphs(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    try:
        result = _query_contract(query_id="fls_stats_v1", params={}, row_limit=1, db_path=db_path)
        rows = list(result.get("rows") or [])
        count = int(rows[0].get("paragraph_count", 0)) if rows else 0
    except Exception:
        return False
    return count > 0


def _preferred_chapters(tokens: list[str]) -> list[str]:
    lower = {token.lower() for token in tokens}
    preferred: list[str] = []

    if lower & {"send", "sync", "thread", "atomic", "fence", "ordering"}:
        preferred.append("Concurrency")
    if lower & {"unsafe", "pointer", "dereference", "undefined"}:
        preferred.append("Unsafety")
    if lower & {"trait", "implementation", "impl"}:
        preferred.append("Implementations")
    if lower & {"closure", "capture", "move"}:
        preferred.append("Expressions")

    return preferred


def _load_exemplar_overrides() -> list[dict[str, Any]]:
    global _EXEMPLAR_OVERRIDES
    if _EXEMPLAR_OVERRIDES is not None:
        return _EXEMPLAR_OVERRIDES
    if not EXEMPLAR_MANIFEST.exists():
        _EXEMPLAR_OVERRIDES = []
        return _EXEMPLAR_OVERRIDES

    payload = json.loads(EXEMPLAR_MANIFEST.read_text(encoding="utf-8"))
    rows = payload.get("exemplars") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        rows = []

    overrides: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rel_path = str(row.get("path", "")).strip()
        if not rel_path:
            continue
        rst_path = GUIDELINES_REPO_ROOT / rel_path
        if not rst_path.exists():
            continue
        text = rst_path.read_text(encoding="utf-8")
        title_match = re.search(r"^(.+)\n=+\n", text, re.MULTILINE)
        fls_match = re.search(r":fls:\s+(fls_[A-Za-z0-9_]+)", text)
        if not title_match or not fls_match:
            continue
        tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]+", title_match.group(1))
            if len(token) > 2
        }
        if not tokens:
            continue
        overrides.append(
            {
                "tokens": tokens,
                "paragraph_id": fls_match.group(1),
                "title": title_match.group(1).strip(),
            }
        )

    _EXEMPLAR_OVERRIDES = overrides
    return _EXEMPLAR_OVERRIDES


def _match_exemplar_override(construct_terms: list[str]) -> str:
    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_]+", " ".join(construct_terms))
        if len(token) > 2
    }
    if not tokens:
        return ""
    best_id = ""
    best_score = 0.0
    for entry in _load_exemplar_overrides():
        exemplar_tokens = entry.get("tokens")
        if not isinstance(exemplar_tokens, set) or not exemplar_tokens:
            continue
        overlap = len(tokens & exemplar_tokens)
        if overlap == 0:
            continue
        score = overlap / float(len(exemplar_tokens))
        if score > best_score:
            best_score = score
            best_id = str(entry.get("paragraph_id", ""))
    return best_id if best_score >= 0.5 else ""


def _fetch_paragraph(paragraph_id: str, db_path: Path) -> dict[str, Any] | None:
    try:
        result = _query_contract(
            query_id="fls_paragraph_lookup_v2",
            params={"statement_id": paragraph_id},
            row_limit=1,
            db_path=db_path,
        )
    except Exception:
        return None
    rows = list(result.get("rows") or [])
    if not rows:
        return None
    row = rows[0]
    return {
        "paragraph_id": str(row.get("paragraph_id", "")),
        "text": str(row.get("text", "")),
        "chapter": str(row.get("chapter", "")),
        "section": str(row.get("section", "")),
        "paragraph_number": str(row.get("paragraph_number", "")),
        "document_link": str(row.get("document_link", "")),
        "section_link": str(row.get("section_link", "")),
        "paragraph_link": str(row.get("paragraph_link", "")),
    }


def search_fls_paragraphs(
    query: str,
    *,
    db_path: Path | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    db_path = _resolve_fls_db_path(db_path)
    if not db_path.exists():
        return []
    _bootstrap_scripts_path()
    from retrieval.operations.query import execute_retrieval_query
    from semantic_backend_client import SemanticBackendConfig

    runtime_db_path, contract_path, query_log_root = _load_fls_runtime()
    result = execute_retrieval_query(
        mode="lexical",
        db_path=db_path if db_path.exists() else runtime_db_path,
        contract_path=contract_path,
        query_log_root=query_log_root,
        query_text=query,
        row_marker="",
        top_k=max(1, int(limit)),
        candidate_limit=max(250, int(limit) * 25),
        allow_degraded=True,
        semantic_config=SemanticBackendConfig(
            base_url="http://127.0.0.1:8080",
            embed_model_id="Qwen/Qwen3-Embedding-4B",
            reranker_model_id="BAAI/bge-reranker-v2-m3",
        ),
        semantic_retries=0,
        persist_semantic_cache=False,
        allow_online_corpus_embedding=False,
        corpus="fls_spec",
    )
    rows = list(result.get("rows") or [])
    out: list[dict[str, Any]] = []
    for row in rows[: max(1, int(limit))]:
        paragraph_id = str(row.get("statement_id", "")).strip()
        if not paragraph_id:
            continue
        details = _fetch_paragraph(paragraph_id, db_path)
        if not details:
            continue
        out.append(
            {
                "paragraph_id": paragraph_id,
                "paragraph_number": str(details.get("paragraph_number", "")),
                "chapter": str(details.get("chapter", "")),
                "section": str(details.get("section", "")),
                "text": str(details.get("text", "")),
                "source_file": "",
                "bm25_rank": float(row.get("bm25_raw", 0.0)),
                "lexical_score": float(row.get("lexical_score", 0.0)),
            }
        )
    return out


def validate_fls_id(paragraph_id: str, *, spec_lock_path: Path = SPEC_LOCK_PATH) -> bool:
    del spec_lock_path
    topology_index = _load_live_topology_index()
    return get_paragraph(topology_index, paragraph_id) is not None


def _packet_query_variants(packet: dict[str, Any], *, construct_terms: list[str]) -> list[str]:
    _bootstrap_scripts_path()
    from retrieval.writer_host.fls_candidate_search import build_query_variants

    variants = build_query_variants(packet)
    out: list[str] = []
    for row in variants:
        if not isinstance(row, dict):
            continue
        query = str(row.get("query", "")).strip()
        if query and query not in out:
            out.append(query)

    tokenized: list[str] = []
    for term in construct_terms[:5]:
        tokenized.extend(re.findall(r"[A-Za-z0-9_]+", term))
    tokenized = tokenized[:8]
    if tokenized:
        strict_query = " AND ".join(f'"{token}"' for token in tokenized)
        broad_query = " OR ".join(f'"{token}"' for token in tokenized)
        if strict_query and strict_query not in out:
            out.insert(0, strict_query)
        if broad_query and broad_query not in out:
            out.append(broad_query)
    return out


def _collect_packet_tokens(
    packet: dict[str, Any], *, construct_terms: list[str]
) -> tuple[set[str], set[str]]:
    query_fields = [
        str(packet.get("title", "")),
        str(packet.get("amplification_text", "")),
        str(packet.get("rationale_text", "")),
        str(packet.get("non_compliant_narrative", "")),
        str(packet.get("compliant_narrative", "")),
        " ".join(list(packet.get("claim_phrases") or [])),
        " ".join(construct_terms),
    ]
    query_tokens = _tokenize_text(" ".join(query_fields))
    code_tokens = _tokenize_code(
        f"{packet.get('non_compliant_code', '')} {packet.get('compliant_code', '')}"
    )
    return query_tokens, code_tokens


def resolve_fls_for_guideline(
    packet: dict[str, Any],
    *,
    db_path: Path | None = None,
    spec_lock_path: Path = SPEC_LOCK_PATH,
    precomputed_candidates: list[dict[str, Any]] | None = None,
    precomputed_variants: list[dict[str, str]] | None = None,
    policy_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    if not construct_terms:
        construct_terms = [
            token for token in re.findall(r"[A-Za-z0-9_]+", str(packet.get("title", ""))) if token
        ]

    terms = [term.strip() for term in construct_terms if term.strip()]
    if not terms:
        return _unresolved("no construct terms provided")

    expected_domains = list(packet.get("expected_domains") or [])
    domains = _resolve_expected_domains(construct_terms=terms, expected_domains=expected_domains)

    collected_rows: list[dict[str, Any]] = []
    query_variants: list[str] = []

    exemplar_override = _match_exemplar_override(terms)
    if exemplar_override and validate_fls_id(exemplar_override, spec_lock_path=spec_lock_path):
        paragraph = _fetch_paragraph(exemplar_override, db_path)
        if paragraph is not None:
            collected_rows.append(
                {
                    **paragraph,
                    "lexical_score": 1.0,
                    "variant_name": "exemplar",
                }
            )

    if precomputed_candidates is not None:
        collected_rows.extend(list(precomputed_candidates))
        pre_rows = list(precomputed_variants or [])
        query_variants = [
            str(item.get("query", "")).strip()
            for item in pre_rows
            if isinstance(item, dict) and str(item.get("query", "")).strip()
        ]
    else:
        query_variants = _packet_query_variants(packet, construct_terms=terms)
        for query in query_variants:
            hits = search_fls_paragraphs(query, db_path=db_path, limit=10)
            for hit in hits:
                collected_rows.append({**hit, "variant_name": query})

    if not collected_rows:
        return _unresolved(f"no FLS match for terms: {terms}")

    merged = _aggregate_candidates(collected_rows)
    _, code_tokens = _collect_packet_tokens(packet, construct_terms=terms)
    field_terms = _field_terms(packet, construct_terms=terms)
    variant_count = len(query_variants)
    policy = _effective_policy(policy_overrides)
    scored = _score_candidates(
        candidates=merged,
        code_tokens=code_tokens,
        field_terms=field_terms,
        domains=domains,
        variant_count=variant_count,
        policy=policy,
    )
    thresholds_raw = policy.get("thresholds")
    thresholds: dict[str, Any] = thresholds_raw if isinstance(thresholds_raw, dict) else {}
    review_raw = policy.get("review_thresholds")
    review_thresholds: dict[str, Any] = review_raw if isinstance(review_raw, dict) else {}
    best = scored[0]
    second = scored[1] if len(scored) > 1 else None
    paragraph_id = str(best.get("paragraph_id", ""))
    second_confidence = float(second.get("confidence", 0.0)) if second else 0.0
    margin = float(best.get("confidence", 0.0)) - second_confidence
    decision = {
        "accepted": False,
        "reason_code": "UNKNOWN",
        "top_candidate_id": paragraph_id,
        "top_score": float(best.get("confidence", 0.0)),
        "second_score": second_confidence,
        "margin": margin,
        "term_overlap": float(best.get("overlap_ratio", 0.0)),
        "weighted_overlap": float(best.get("weighted_overlap", 0.0)),
        "term_hits": int(best.get("overlap_hits", 0)),
        "high_trust_field_hits": int(best.get("high_trust_field_hits", 0)),
        "code_overlap": float(best.get("code_overlap_ratio", 0.0)),
        "variant_coverage": float(best.get("variant_coverage", 0.0)),
        "variant_count": int(best.get("variant_count", 0) or 0),
        "chapter_match": bool(best.get("chapter_match", False)),
        "expected_domains": sorted(domains),
        "thresholds": dict(thresholds),
        "review_thresholds": dict(review_thresholds),
        "publish_accept": False,
        "review_candidate": False,
        "used_llm_proposal": False,
        "top_candidates": [
            {
                "paragraph_id": str(row.get("paragraph_id", "")),
                "confidence": float(row.get("confidence", 0.0)),
                "chapter": str(row.get("chapter", "")),
                "term_overlap": float(row.get("overlap_ratio", 0.0)),
                "high_trust_field_hits": int(row.get("high_trust_field_hits", 0)),
                "per_field_overlap": (
                    row.get("per_field_overlap", {})
                    if isinstance(row.get("per_field_overlap"), dict)
                    else {}
                ),
                "variant_coverage": float(row.get("variant_coverage", 0.0)),
            }
            for row in scored[:5]
        ],
    }
    if not paragraph_id:
        decision["reason_code"] = "MISSING_PARAGRAPH_ID"
        return _unresolved("search returned no paragraph_id", decision=decision)

    min_term_hits = int(
        _policy_threshold(policy, name="min_term_hits", default=float(MIN_TERM_HITS))
    )
    min_weighted_overlap = _policy_threshold(
        policy,
        name="min_weighted_overlap",
        default=MIN_TERM_OVERLAP,
    )
    min_high_trust_matches = int(
        _policy_threshold(policy, name="min_high_trust_field_match", default=1.0)
    )
    min_confidence = _policy_threshold(
        policy,
        name="min_confidence_score",
        default=MIN_CONFIDENCE_SCORE,
    )
    min_margin = _policy_threshold(
        policy,
        name="min_confidence_margin",
        default=MIN_CONFIDENCE_MARGIN,
    )

    review_min_confidence = _policy_threshold(
        policy,
        name="min_confidence_score",
        default=max(0.0, min_confidence * 0.77),
        section="review_thresholds",
    )
    review_min_weighted_overlap = _policy_threshold(
        policy,
        name="min_weighted_overlap",
        default=max(0.0, min_weighted_overlap * 0.71),
        section="review_thresholds",
    )
    review_min_variant_coverage = int(
        _policy_threshold(
            policy,
            name="min_variant_coverage",
            default=max(1.0, float(MIN_VARIANT_COVERAGE - 1)),
            section="review_thresholds",
        )
    )
    review_min_high_trust = int(
        _policy_threshold(
            policy,
            name="min_high_trust_field_match",
            default=1.0,
            section="review_thresholds",
        )
    )
    review_candidate = (
        float(best.get("confidence", 0.0)) >= review_min_confidence
        and float(best.get("weighted_overlap", 0.0)) >= review_min_weighted_overlap
        and int(best.get("variant_count", 0) or 0) >= review_min_variant_coverage
        and int(best.get("high_trust_field_hits", 0) or 0) >= review_min_high_trust
        and (not domains or bool(best.get("chapter_match", False)))
    )
    decision["review_candidate"] = review_candidate

    if (
        int(best.get("overlap_hits", 0)) < min_term_hits
        or float(best.get("weighted_overlap", 0.0)) < min_weighted_overlap
    ):
        decision["reason_code"] = "LOW_WEIGHTED_OVERLAP"
        return _unresolved("top candidate failed weighted-overlap gate", decision=decision)

    if int(best.get("high_trust_field_hits", 0)) < min_high_trust_matches:
        decision["reason_code"] = "INSUFFICIENT_HIGH_TRUST_FIELD_MATCH"
        return _unresolved("top candidate failed high-trust field evidence gate", decision=decision)

    required_variant_coverage = int(
        packet.get(
            "min_variant_coverage",
            int(
                _policy_threshold(
                    policy,
                    name="min_variant_coverage",
                    default=float(MIN_VARIANT_COVERAGE),
                )
            ),
        )
        or 0
    )
    if int(best.get("variant_count", 0) or 0) < required_variant_coverage:
        decision["reason_code"] = "INSUFFICIENT_VARIANT_COVERAGE"
        return _unresolved("top candidate failed variant coverage gate", decision=decision)

    if domains and not bool(best.get("chapter_match", False)):
        decision["reason_code"] = "CHAPTER_MISMATCH"
        return _unresolved("top candidate chapter mismatches expected domain", decision=decision)

    if float(best.get("confidence", 0.0)) < min_confidence:
        decision["reason_code"] = "LOW_CONFIDENCE_SCORE"
        return _unresolved("top candidate confidence score below threshold", decision=decision)

    if second is not None and margin < min_margin:
        decision["reason_code"] = "LOW_CONFIDENCE_MARGIN"
        return _unresolved("top candidate confidence margin below threshold", decision=decision)

    live_topology = get_live_topology_membership(paragraph_id=paragraph_id)
    if live_topology is None:
        decision["reason_code"] = "INVALID_ID"
        unresolved = _unresolved(
            f"ID {paragraph_id} not present in live topology",
            decision=decision,
        )
        unresolved["stale_candidate"] = paragraph_id
        return unresolved

    decision["accepted"] = True
    decision["publish_accept"] = True
    decision["review_candidate"] = True
    decision["reason_code"] = "ACCEPTED"
    return {
        "paragraph_id": paragraph_id,
        "text": str(best.get("text", "")),
        "chapter": str(best.get("chapter", "")),
        "section": str(best.get("section", "")),
        "paragraph_number": str(best.get("paragraph_number", "")),
        "document_link": str(
            best.get("document_link", "") or live_topology.get("document_link", "")
        ),
        "section_link": str(best.get("section_link", "") or live_topology.get("section_link", "")),
        "paragraph_link": str(
            best.get("paragraph_link", "") or live_topology.get("paragraph_link", "")
        ),
        "decision": decision,
    }


def resolve_fls_for_construct(
    construct_terms: list[str],
    *,
    db_path: Path | None = None,
    spec_lock_path: Path = SPEC_LOCK_PATH,
    expected_domains: list[str] | None = None,
    policy_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    packet = {
        "title": " ".join(construct_terms),
        "construct_terms": list(construct_terms),
        "expected_domains": list(expected_domains or []),
        "claim_phrases": [],
        "amplification_text": "",
        "rationale_text": "",
        "non_compliant_narrative": "",
        "non_compliant_code": "",
        "compliant_narrative": "",
        "compliant_code": "",
        "min_variant_coverage": 1,
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
