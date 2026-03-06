"""FLS paragraph lookup from the `fls_spec` SQLite knowledge base."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

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

MIN_CONFIDENCE_SCORE = 0.52
MIN_CONFIDENCE_MARGIN = 0.06
MIN_TERM_OVERLAP = 0.18
MIN_TERM_HITS = 2

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


def _resolve_fls_db_path(db_path: Path | None = None) -> Path:
    if db_path is not None:
        return db_path
    if FLS_CANONICAL_DB_PATH.exists():
        return FLS_CANONICAL_DB_PATH
    return FLS_COMPAT_DB_PATH


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
    *, candidates: list[dict[str, Any]], query_tokens: set[str], domains: set[str]
) -> list[dict[str, Any]]:
    lexical_values = [float(row.get("lexical_score", 0.0) or 0.0) for row in candidates]
    lexical_min = min(lexical_values) if lexical_values else 0.0
    lexical_max = max(lexical_values) if lexical_values else 0.0
    scored: list[dict[str, Any]] = []
    for row in candidates:
        lexical_raw = float(row.get("lexical_score", 0.0) or 0.0)
        if lexical_max > lexical_min:
            lexical_norm = (lexical_raw - lexical_min) / (lexical_max - lexical_min)
        else:
            lexical_norm = 1.0 if lexical_raw > 0.0 else 0.0
        paragraph_tokens = _tokenize_text(str(row.get("text", "")))
        overlap_hits = len(query_tokens & paragraph_tokens)
        overlap_ratio = float(overlap_hits) / float(len(query_tokens)) if query_tokens else 0.0
        chapter = str(row.get("chapter", ""))
        chapter_match = _candidate_chapter_match(chapter=chapter, domains=domains)
        confidence = (
            (0.50 * overlap_ratio) + (0.30 * lexical_norm) + (0.20 if chapter_match else 0.0)
        )
        scored.append(
            {
                **row,
                "overlap_hits": overlap_hits,
                "overlap_ratio": round(overlap_ratio, 6),
                "lexical_score_raw": lexical_raw,
                "lexical_score_norm": round(lexical_norm, 6),
                "chapter_match": chapter_match,
                "confidence": round(confidence, 6),
            }
        )
    scored.sort(
        key=lambda row: (
            -float(row.get("confidence", 0.0)),
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
            query_id="fls_paragraph_lookup_v1",
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
    if not spec_lock_path.exists():
        return True

    try:
        lock_data = json.loads(spec_lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True

    for document in lock_data.get("documents", []):
        for section in document.get("sections", []):
            if section.get("id") == paragraph_id:
                return True
            for paragraph in section.get("paragraphs", []):
                if paragraph.get("id") == paragraph_id:
                    return True
    return False


def resolve_fls_for_construct(
    construct_terms: list[str],
    *,
    db_path: Path | None = None,
    spec_lock_path: Path = SPEC_LOCK_PATH,
    expected_domains: list[str] | None = None,
) -> dict[str, Any]:
    db_path = _resolve_fls_db_path(db_path)
    if not _db_has_paragraphs(db_path):
        raise RuntimeError(
            "FLS DB unavailable or empty; build "
            ".cache/sqlite_kb/current/fls_spec.db before FLS resolution."
        )

    terms = [term.strip() for term in construct_terms if term.strip()]
    if not terms:
        return _unresolved("no construct terms provided")

    domains = _resolve_expected_domains(construct_terms=terms, expected_domains=expected_domains)

    scored_candidates: list[dict[str, Any]] = []
    exemplar_override = _match_exemplar_override(terms)
    if exemplar_override and validate_fls_id(exemplar_override, spec_lock_path=spec_lock_path):
        paragraph = _fetch_paragraph(exemplar_override, db_path)
        if paragraph is not None:
            paragraph["lexical_score"] = 1.0
            paragraph["proposal_source"] = "exemplar"
            scored_candidates.append(paragraph)

    tokenized: list[str] = []
    for term in terms[:5]:
        tokenized.extend(re.findall(r"[A-Za-z0-9_]+", term))
    expanded: list[str] = []
    for token in tokenized:
        expanded.append(token)
        lowered = token.lower()
        if lowered == "implementation":
            expanded.extend(["impl", "implementations"])
        elif lowered == "trait":
            expanded.append("traits")
        elif lowered == "closure":
            expanded.append("closures")
    tokenized = expanded
    tokenized = tokenized[:8]

    strict_query = " AND ".join(f'"{token}"' for token in tokenized)
    broad_query = " OR ".join(f'"{token}"' for token in tokenized)

    results: list[dict[str, Any]] = []
    first = search_fls_paragraphs(strict_query, db_path=db_path, limit=10)
    results.extend(first)
    if not results:
        results.extend(search_fls_paragraphs(broad_query, db_path=db_path, limit=10))
    if not results:
        for term in terms:
            term_results = search_fls_paragraphs(term, db_path=db_path, limit=10)
            if term_results:
                results.extend(term_results)
                break

    if not results:
        return _unresolved(f"no FLS match for terms: {terms}")

    by_id: dict[str, dict[str, Any]] = {}
    for row in scored_candidates + results:
        paragraph_id = str(row.get("paragraph_id", "")).strip()
        if not paragraph_id:
            continue
        if paragraph_id not in by_id:
            by_id[paragraph_id] = row
            continue
        prior = by_id[paragraph_id]
        if float(row.get("lexical_score", 0.0) or 0.0) > float(
            prior.get("lexical_score", 0.0) or 0.0
        ):
            by_id[paragraph_id] = row
    merged = list(by_id.values())

    query_tokens = _tokenize_text(" ".join(terms))
    scored = _score_candidates(candidates=merged, query_tokens=query_tokens, domains=domains)
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
        "term_hits": int(best.get("overlap_hits", 0)),
        "chapter_match": bool(best.get("chapter_match", False)),
        "expected_domains": sorted(domains),
        "used_llm_proposal": False,
    }
    if not paragraph_id:
        decision["reason_code"] = "MISSING_PARAGRAPH_ID"
        return _unresolved("search returned no paragraph_id", decision=decision)

    if (
        int(best.get("overlap_hits", 0)) < MIN_TERM_HITS
        or float(best.get("overlap_ratio", 0.0)) < MIN_TERM_OVERLAP
    ):
        decision["reason_code"] = "LOW_TERM_OVERLAP"
        return _unresolved("top candidate failed term-overlap gate", decision=decision)

    if domains and not bool(best.get("chapter_match", False)):
        decision["reason_code"] = "CHAPTER_MISMATCH"
        return _unresolved("top candidate chapter mismatches expected domain", decision=decision)

    if float(best.get("confidence", 0.0)) < MIN_CONFIDENCE_SCORE:
        decision["reason_code"] = "LOW_CONFIDENCE_SCORE"
        return _unresolved("top candidate confidence score below threshold", decision=decision)

    if second is not None and margin < MIN_CONFIDENCE_MARGIN:
        decision["reason_code"] = "LOW_CONFIDENCE_MARGIN"
        return _unresolved("top candidate confidence margin below threshold", decision=decision)

    if not validate_fls_id(paragraph_id, spec_lock_path=spec_lock_path):
        decision["reason_code"] = "INVALID_ID"
        unresolved = _unresolved(
            f"ID {paragraph_id} not present in current spec.lock",
            decision=decision,
        )
        unresolved["stale_candidate"] = paragraph_id
        return unresolved

    decision["accepted"] = True
    decision["reason_code"] = "ACCEPTED"
    return {
        "paragraph_id": paragraph_id,
        "text": str(best.get("text", "")),
        "chapter": str(best.get("chapter", "")),
        "section": str(best.get("section", "")),
        "paragraph_number": str(best.get("paragraph_number", "")),
        "decision": decision,
    }


def get_fls_db_stats(db_path: Path | None = None) -> dict[str, Any]:
    db_path = _resolve_fls_db_path(db_path)
    if not db_path.exists():
        return {"available": False, "source": "none"}
    result = _query_contract(query_id="fls_stats_v1", params={}, row_limit=1, db_path=db_path)
    rows = list(result.get("rows") or [])
    top = rows[0] if rows else {}
    paragraph_count = int(top.get("paragraph_count", 0) or 0)
    chapter_count = int(top.get("chapter_count", 0) or 0)
    commit_sha = str(top.get("commit_sha", "") or "")
    built_at = str(top.get("built_at", "") or "")

    stats: dict[str, Any] = {
        "available": paragraph_count > 0,
        "source": "fls_spec_db" if paragraph_count > 0 else "none",
        "paragraph_count": paragraph_count,
        "chapter_count": chapter_count,
    }
    if commit_sha:
        stats["commit_sha"] = commit_sha
    if built_at:
        stats["built_at"] = built_at
    return stats
