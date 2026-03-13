from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from context.fls_topology import (
    get_paragraph,
    paragraph_ids_for_document,
    paragraph_ids_for_section,
)

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
PHRASE_STOPWORDS = {
    "a",
    "an",
    "and",
    "any",
    "as",
    "in",
    "of",
    "the",
    "to",
    "where",
}
GENERIC_PHRASE_TOKENS = {"expression", "operand", "pointer", "target", "type", "value"}
LOW_SIGNAL_RUNTIME_TOKENS = {
    "alloc",
    "attribute",
    "attributes",
    "built",
    "compilation",
    "core",
    "entities",
    "entity",
    "hint",
    "module",
    "modules",
    "prelude",
    "preludes",
    "scope",
    "scopes",
    "statement",
    "statements",
    "std",
}
STAGE_ORDER = ("section", "document", "global")
MODE_ORDER = ("lexical", "semantic", "hybrid")
MODE_PREFERENCE = {"hybrid": 0, "semantic": 1, "lexical": 2, "supplemental": 3}
ADVANCEMENT_NO_QUALIFYING = "NO_QUALIFYING_CANDIDATES"
ADVANCEMENT_STAGE_SUCCESS = "TERMINAL_STAGE_SUCCESS"
ADVANCEMENT_GLOBAL_FALLBACK = "GLOBAL_FALLBACK_REQUIRED"
ADVANCEMENT_VALIDATION_ONLY = "VALIDATION_ONLY_CONTINUATION"
REASON_ACCEPTED = "ACCEPTED"
REASON_REVIEW = "REVIEW_REQUIRED"
REASON_NO_QUALIFYING = "NO_QUALIFYING_CANDIDATES"
REASON_WEAK = "WEAK_CANDIDATE"
REASON_AMBIGUOUS = "AMBIGUOUS_TOP_CANDIDATES"
REASON_SCOPED_NO_QUALIFYING = "SCOPED_STAGE_NO_QUALIFYING_CANDIDATES"
REASON_SCOPED_WEAK_SCOPE = "SCOPED_STAGE_NON_TERMINAL_WEAK_SCOPE"
REASON_SCOPED_GLOSSARY = "SCOPED_STAGE_NON_TERMINAL_GLOSSARY_WINNER"
REASON_SCOPED_WEAK_PHRASE = "SCOPED_STAGE_NON_TERMINAL_WEAK_PHRASE_SUPPORT"
REASON_SCOPED_OVERRIDE = "SCOPED_STAGE_TERMINAL_STRONG_NON_GLOSSARY_OVERRIDE"
REASON_TERMINAL_STAGE_SUCCESS = "TERMINAL_STAGE_SUCCESS"
REASON_GLOBAL_STAGE = "GLOBAL_AUTHORITATIVE_STAGE"
DECLARED_QUERY_INPUT_FIELDS = (
    "governing_obligation",
    "construct_terms",
    "supporting_phrases",
    "code_tokens",
    "prior_documents",
    "prior_sections",
)


def _trace_path(policy: dict[str, Any]) -> Path | None:
    raw = str(policy.get("trace_path", "")).strip()
    return Path(raw) if raw else None


def _trace_event(policy: dict[str, Any], event: dict[str, Any]) -> None:
    path = _trace_path(policy)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": round(time.time(), 6), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=False) + "\n")


def _bootstrap_scripts_path(project_root: Path) -> None:
    scripts = project_root / "scripts"
    value = str(scripts)
    if value not in sys.path:
        sys.path.insert(0, value)


def _deep_merge_policy(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_policy(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _load_policy(project_root: Path, *, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = (
        yaml.safe_load(
            (project_root / "config" / "fls_resolution_policy.yaml").read_text(encoding="utf-8")
        )
        or {}
    )
    policy = dict(payload.get("ws7_policy") or {})
    if overrides:
        policy = _deep_merge_policy(policy, overrides)
    return policy


def _load_runtime_components(project_root: Path) -> tuple[Any, Any]:
    _bootstrap_scripts_path(project_root)
    from scripts.retrieval.operations.query import execute_retrieval_query

    from semantic_backend_client import SemanticBackendConfig

    return execute_retrieval_query, SemanticBackendConfig


def _text_list(values: Any) -> list[str]:
    out: list[str] = []
    for value in list(values or []):
        normalized = str(value).strip()
        if not normalized or normalized in out:
            continue
        out.append(normalized)
    return out


def _project_query_inputs(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "governing_obligation": str(packet.get("governing_obligation", "")).strip(),
        "construct_terms": _text_list(packet.get("construct_terms")),
        "supporting_phrases": _text_list(packet.get("supporting_phrases")),
        "code_tokens": _text_list(packet.get("code_tokens")),
        "prior_documents": [
            dict(row) for row in list(packet.get("prior_documents") or []) if isinstance(row, dict)
        ],
        "prior_sections": [
            dict(row) for row in list(packet.get("prior_sections") or []) if isinstance(row, dict)
        ],
    }


def _build_query_text(project_root: Path, packet: dict[str, Any]) -> str:
    del project_root
    projection = _project_query_inputs(packet)
    pieces: list[str] = []
    obligation = str(projection["governing_obligation"])
    if obligation:
        pieces.append(obligation)
    supporting_phrases = list(projection["supporting_phrases"])
    pieces.extend(supporting_phrases)
    construct_terms = list(projection["construct_terms"])
    if construct_terms:
        pieces.append(" ".join(construct_terms))
    attribute_context = any(
        "attribute" in str(value).lower()
        for value in [obligation, *supporting_phrases, *construct_terms]
    )
    if attribute_context:
        attribute_hints: list[str] = []
        seen_hints: set[str] = set()
        for term in construct_terms:
            tokens = [
                token
                for token in _tokenize_text(term)
                if token not in PHRASE_STOPWORDS and token not in LOW_SIGNAL_RUNTIME_TOKENS
            ]
            if len(tokens) != 1:
                continue
            hint = f"attribute {tokens[0]}"
            if hint in seen_hints:
                continue
            seen_hints.add(hint)
            attribute_hints.append(hint)
        pieces.extend(attribute_hints)
    code_tokens = list(projection["code_tokens"])
    if code_tokens:
        pieces.append(" ".join(code_tokens))
    return "\n".join(piece for piece in pieces if piece).strip()


def _tokenize_text(text: str) -> set[str]:
    def _normalize_token(token: str) -> str:
        lowered = token.lower()
        if len(lowered) > 4 and lowered.endswith("s") and not lowered.endswith("ss"):
            return lowered[:-1]
        return lowered

    return {_normalize_token(token) for token in TOKEN_RE.findall(str(text or ""))}


def _meaningful_runtime_tokens(tokens: set[str]) -> set[str]:
    return {
        token
        for token in tokens
        if token and token not in PHRASE_STOPWORDS and token not in LOW_SIGNAL_RUNTIME_TOKENS
    }


def _tokenize_packet(packet: dict[str, Any]) -> dict[str, set[str]]:
    supporting_phrase_texts = [
        str(value).strip()
        for value in list(packet.get("supporting_phrases") or [])
        if str(value).strip()
    ]
    construct_terms = {
        str(value).strip().lower()
        for value in list(packet.get("construct_terms") or [])
        if str(value).strip()
    }
    support_tokens = _tokenize_text(
        " ".join(str(value) for value in list(packet.get("supporting_phrases") or []))
    )
    governing_tokens = _tokenize_text(str(packet.get("governing_obligation", "")))
    code_tokens = {
        str(value).strip().lower()
        for value in list(packet.get("code_tokens") or [])
        if str(value).strip()
    }
    text_tokens = set().union(construct_terms, support_tokens, governing_tokens)
    return {
        "construct_terms": construct_terms,
        "support_tokens": support_tokens,
        "governing_tokens": governing_tokens,
        "code_tokens": code_tokens,
        "text_tokens": text_tokens,
        "supporting_phrase_texts": set(supporting_phrase_texts),
    }


def _normalized_chunk_text(row: dict[str, Any]) -> str:
    return str(row.get("chunk_text", row.get("text", "")) or "").strip().lower()


def _phrase_ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    if n <= 0 or len(tokens) < n:
        return set()
    return {tuple(tokens[index : index + n]) for index in range(len(tokens) - n + 1)}


def _ordered_subsequence_coverage(needle_tokens: list[str], haystack_tokens: list[str]) -> float:
    if not needle_tokens:
        return 0.0
    haystack_index = 0
    matched = 0
    for token in needle_tokens:
        while haystack_index < len(haystack_tokens) and haystack_tokens[haystack_index] != token:
            haystack_index += 1
        if haystack_index >= len(haystack_tokens):
            break
        matched += 1
        haystack_index += 1
    return round(matched / max(1, len(needle_tokens)), 6)


def _phrase_evidence_score(row: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    def _targets_after_to(tokens: list[str]) -> set[str]:
        targets: set[str] = set()
        for index, token in enumerate(tokens[:-1]):
            if token != "to":
                continue
            for candidate in tokens[index + 1 : index + 3]:
                if candidate in PHRASE_STOPWORDS:
                    continue
                targets.add(candidate)
                break
        return targets

    def _normalize_phrase_token(token: str) -> str:
        lowered = token.lower()
        if len(lowered) > 4 and lowered.endswith("s") and not lowered.endswith("ss"):
            return lowered[:-1]
        return lowered

    chunk_text = _normalized_chunk_text(row)
    chunk_tokens = [_normalize_phrase_token(token) for token in TOKEN_RE.findall(chunk_text)]
    filtered_chunk_tokens = [token for token in chunk_tokens if token not in PHRASE_STOPWORDS]
    chunk_targets = _targets_after_to(chunk_tokens)
    best = {
        "phrase_evidence_score": 0.0,
        "best_phrase_text": "",
        "best_phrase_exact_match": 0.0,
        "best_phrase_ordered_overlap": 0.0,
        "best_phrase_bigram_score": 0.0,
        "best_phrase_trigram_score": 0.0,
        "best_phrase_ngram_score": 0.0,
    }
    for phrase in list(packet.get("supporting_phrases") or []):
        phrase_text = str(phrase or "").strip().lower()
        if not phrase_text:
            continue
        phrase_tokens = [_normalize_phrase_token(token) for token in TOKEN_RE.findall(phrase_text)]
        if not phrase_tokens:
            continue
        filtered_phrase_tokens = [token for token in phrase_tokens if token not in PHRASE_STOPWORDS]
        phrase_specificity = len(
            [token for token in filtered_phrase_tokens if token not in GENERIC_PHRASE_TOKENS]
        ) / max(1, len(filtered_phrase_tokens))
        phrase_targets = _targets_after_to(phrase_tokens)
        exact_phrase_match = 1.0 if phrase_text in chunk_text else 0.0
        ordered_overlap_score = _ordered_subsequence_coverage(
            filtered_phrase_tokens or phrase_tokens,
            filtered_chunk_tokens or chunk_tokens,
        )
        ngram_source = [
            token for token in phrase_tokens if token not in PHRASE_STOPWORDS
        ] or phrase_tokens
        ngram_target = [
            token for token in chunk_tokens if token not in PHRASE_STOPWORDS
        ] or chunk_tokens
        bigrams = _phrase_ngrams(ngram_source, 2)
        trigrams = _phrase_ngrams(ngram_source, 3)
        bigram_score = (
            round(
                len(bigrams.intersection(_phrase_ngrams(ngram_target, 2))) / max(1, len(bigrams)), 6
            )
            if bigrams
            else 0.0
        )
        trigram_score = (
            round(
                len(trigrams.intersection(_phrase_ngrams(ngram_target, 3))) / max(1, len(trigrams)),
                6,
            )
            if trigrams
            else 0.0
        )
        ngram_score = round(max(bigram_score, trigram_score), 6)
        local_phrase_score = max(
            exact_phrase_match, 0.65 * ordered_overlap_score + 0.35 * ngram_score
        )
        if phrase_targets and phrase_targets.intersection(chunk_targets):
            local_phrase_score += 0.15 * max(0.35, phrase_specificity)
        local_phrase_score *= max(0.35, phrase_specificity)
        local_phrase_score = round(max(0.0, min(1.0, local_phrase_score)), 6)
        if local_phrase_score > float(best["phrase_evidence_score"]):
            best = {
                "phrase_evidence_score": local_phrase_score,
                "best_phrase_text": phrase,
                "best_phrase_exact_match": round(exact_phrase_match, 6),
                "best_phrase_ordered_overlap": round(ordered_overlap_score, 6),
                "best_phrase_bigram_score": bigram_score,
                "best_phrase_trigram_score": trigram_score,
                "best_phrase_ngram_score": ngram_score,
            }
    return best


def _parse_role_payload(raw: Any) -> list[dict[str, str]]:
    try:
        payload = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    out: list[dict[str, str]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "text": str(row.get("text", "")).strip(),
                "target": str(row.get("target", "")).strip(),
            }
        )
    return out


def _overlap_score(candidate_text: str, packet_tokens: dict[str, set[str]]) -> float:
    query_tokens = _meaningful_runtime_tokens(set(packet_tokens["text_tokens"]))
    if not query_tokens:
        return 0.0
    candidate_tokens = _meaningful_runtime_tokens(_tokenize_text(candidate_text))
    if not candidate_tokens:
        return 0.0
    overlap = len(candidate_tokens.intersection(query_tokens))
    score = overlap / max(1, len(query_tokens))
    return round(max(0.0, min(1.0, score)), 6)


def _prior_score(rows: list[dict[str, Any]], *, key: str, value: str, stage: str) -> float:
    if stage == "global":
        return 0.0
    for row in rows:
        if str(row.get(key, "")).strip() == value:
            return round(float(row.get("score", 0.0) or 0.0), 6)
    return 0.0


def _role_match_score(
    role_rows: list[dict[str, str]],
    *,
    packet_terms: set[str],
) -> tuple[float, list[dict[str, str]]]:
    filtered_packet_terms = _meaningful_runtime_tokens(set(packet_terms))
    if not role_rows or not filtered_packet_terms:
        return 0.0, []
    matched: list[dict[str, str]] = []
    for row in role_rows:
        row_tokens = _meaningful_runtime_tokens(
            _tokenize_text(" ".join((row.get("text", ""), row.get("target", ""))))
        )
        if row_tokens.intersection(filtered_packet_terms):
            matched.append(row)
    score = min(1.0, len(matched) / max(1, len(role_rows))) if matched else 0.0
    return round(float(score), 6), matched


def _code_evidence_score(
    row: dict[str, Any], *, packet_code_terms: set[str]
) -> tuple[float, list[str]]:
    normalized_terms: set[str] = set()
    for term in packet_code_terms:
        normalized_terms.update(_tokenize_text(str(term)))
    filtered_terms = _meaningful_runtime_tokens(normalized_terms)
    if not filtered_terms:
        return 0.0, []
    row_tokens = _meaningful_runtime_tokens(
        _tokenize_text(str(row.get("chunk_text", row.get("text", "")) or ""))
    )
    matched = sorted(filtered_terms.intersection(row_tokens))
    if not matched:
        return 0.0, []
    score = min(1.0, len(matched) / max(1, min(3, len(filtered_terms))))
    return round(float(score), 6), matched


def _is_glossary_candidate(row: dict[str, Any]) -> bool:
    content_type = str(row.get("content_type", "")).strip().lower()
    if content_type == "glossary":
        return True
    document_link = str(row.get("document_link", "")).lower()
    section_link = str(row.get("section_link", "")).lower()
    section_heading = str(row.get("section_heading", row.get("section", ""))).lower()
    return (
        "glossary" in document_link or "glossary" in section_link or "glossary" in section_heading
    )


def _candidate_content_type(row: dict[str, Any]) -> str:
    content_type = str(row.get("content_type", "")).strip().lower()
    if content_type:
        return content_type
    if _is_glossary_candidate(row):
        return "glossary"
    return "normative"


def _candidate_identity(row: dict[str, Any]) -> dict[str, str]:
    return {
        "chunk_uid": str(row.get("chunk_uid", "")).strip(),
        "paragraph_id": str(row.get("paragraph_id", row.get("chunk_uid", ""))).strip(),
        "paragraph_link": str(row.get("paragraph_link", "")).strip(),
    }


def _mode_row_ref(*, mode: str, stage: str, row: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "stage": stage,
        "mode": mode,
        "rank": int(rank),
        "chunk_uid": str(row.get("chunk_uid", "")).strip(),
        "paragraph_id": str(row.get("paragraph_id", row.get("chunk_uid", ""))).strip(),
        "relevance_score": float(row.get("relevance_score", 0.0) or 0.0),
        "lexical_score": float(row.get("lexical_score", 0.0) or 0.0),
        "semantic_score": float(row.get("semantic_score", 0.0) or 0.0),
        "reranker_score": float(row.get("reranker_score", 0.0) or 0.0),
    }


CANONICAL_FIELDS = (
    "chunk_uid",
    "paragraph_id",
    "paragraph_link",
    "document_link",
    "section_link",
    "section_heading",
    "chapter",
    "section",
    "paragraph_number",
    "text",
    "chunk_text",
    "defined_terms_json",
    "term_refs_json",
    "syntax_defs_json",
    "syntax_refs_json",
    "std_refs_json",
    "source_anchor",
    "checksum",
)
IDENTITY_FIELDS = (
    "paragraph_id",
    "paragraph_link",
    "document_link",
    "section_link",
    "checksum",
)
TEXT_FIELDS = ("text", "chunk_text")
ROLE_PAYLOAD_FIELDS = (
    "defined_terms_json",
    "term_refs_json",
    "syntax_defs_json",
    "syntax_refs_json",
    "std_refs_json",
)
DESCRIPTIVE_FIELDS = (
    "chunk_uid",
    "section_heading",
    "chapter",
    "section",
    "paragraph_number",
    "source_anchor",
)
MERGEABLE_FIELDS = tuple(field for field in CANONICAL_FIELDS if field not in IDENTITY_FIELDS)


def _canonical_value_key(value: Any) -> tuple[int, str]:
    normalized = str(value or "").strip()
    return (len(normalized), normalized)


def _mode_sort_key(mode: str) -> tuple[int, str]:
    return (MODE_PREFERENCE.get(mode, 99), str(mode))


def _row_field_value_kind(field: str, value: Any) -> tuple[int, int, str]:
    normalized = str(value or "").strip()
    if field in ROLE_PAYLOAD_FIELDS:
        try:
            payload = json.loads(normalized or "[]")
        except json.JSONDecodeError:
            payload = []
        size = len(payload) if isinstance(payload, list) else 0
        return (size, len(normalized), normalized)
    return (0, *_canonical_value_key(normalized))


def _choose_field_value(rows_by_mode: dict[str, dict[str, Any]], field: str) -> tuple[Any, str]:
    candidates: list[tuple[tuple[int, int, str], tuple[int, str], str, Any]] = []
    for mode, row in rows_by_mode.items():
        normalized = str(row.get(field, "")).strip()
        if not normalized:
            continue
        candidates.append(
            (
                _row_field_value_kind(field, row.get(field)),
                _mode_sort_key(mode),
                mode,
                row.get(field),
            )
        )
    if not candidates:
        return "", ""
    candidates.sort(key=lambda item: (-item[0][0], -item[0][1], item[1], item[0][2]))
    _, _, mode, value = candidates[0]
    return value, mode


def _identity_values(rows_by_mode: dict[str, dict[str, Any]], field: str) -> list[str]:
    values = {
        str(row.get(field, "")).strip()
        for row in rows_by_mode.values()
        if str(row.get(field, "")).strip()
    }
    return sorted(values)


def _merge_canonical_row(
    rows_by_mode: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    canonical: dict[str, Any] = {}
    identity_conflicts: dict[str, list[str]] = {}
    ranking_conflicts: dict[str, list[str]] = {}
    selected_values: dict[str, str] = {}
    selected_modes: dict[str, str] = {}
    for field in IDENTITY_FIELDS:
        values = _identity_values(rows_by_mode, field)
        if len(values) > 1:
            identity_conflicts[field] = values
        selected, selected_mode = _choose_field_value(rows_by_mode, field)
        canonical[field] = selected
        selected_values[field] = str(selected or "").strip()
        selected_modes[field] = selected_mode
    for field in MERGEABLE_FIELDS:
        values = _identity_values(rows_by_mode, field)
        if field in TEXT_FIELDS + ROLE_PAYLOAD_FIELDS and len(values) > 1:
            ranking_conflicts[field] = values
        canonical[field], selected_modes[field] = _choose_field_value(rows_by_mode, field)
        selected_values[field] = str(canonical[field] or "").strip()
    return canonical, {
        "identity_conflicts": identity_conflicts,
        "ranking_conflicts": ranking_conflicts,
        "selected_values": selected_values,
        "selected_modes": selected_modes,
    }


def _candidate_ids(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        mode_row_refs = {
            str(ref.get("mode", "")): {
                "rank": int(ref.get("rank", 0) or 0),
                "stage": str(ref.get("stage", "")),
                "chunk_uid": str(ref.get("chunk_uid", "")),
                "paragraph_id": str(ref.get("paragraph_id", "")),
            }
            for ref in list(candidate.get("mode_row_refs") or [])
        }
        out.append(
            {
                "chunk_uid": str(candidate.get("chunk_uid", "")),
                "paragraph_id": str(candidate.get("paragraph_id", "")),
                "paragraph_link": str(candidate.get("paragraph_link", "")),
                "first_seen_stage": str(candidate.get("first_seen_stage", "")),
                "seen_in_modes": list(candidate.get("seen_in_modes") or []),
                "mode_row_refs": mode_row_refs,
            }
        )
    return out


def _artifact_mode_ref(
    *,
    stage: str,
    query_text: str,
    mode: str,
    result: dict[str, Any],
    qualifying_count: int,
) -> dict[str, Any]:
    return {
        "requested_mode": mode,
        "executed_mode": str(result.get("executed_mode", mode)),
        "returned_candidate_count": int(
            result.get("row_count", len(result.get("rows") or [])) or 0
        ),
        "qualifying_candidate_count": int(qualifying_count),
        "retrieval_result_ref": {
            "stage": stage,
            "query_text": query_text,
            "scope": dict(result.get("scope") or {}),
            "qualifying_paragraph_ids": [],
            "rows": [
                {
                    "chunk_uid": str(row.get("chunk_uid", "")),
                    "paragraph_id": str(row.get("paragraph_id", row.get("chunk_uid", ""))),
                    "paragraph_link": str(row.get("paragraph_link", "")),
                }
                for row in list(result.get("rows") or [])
            ],
        },
    }


def _candidate_meets_gate_floor(candidate: dict[str, Any], *, policy: dict[str, Any]) -> bool:
    gating_cfg = dict(policy.get("gating") or {})
    review_total_min = float(gating_cfg.get("review_total_score_min", 0.0) or 0.0)
    return float(candidate.get("total_score", 0.0) or 0.0) >= review_total_min


def _merge_stage_candidates(
    *,
    stage: str,
    mode_results: dict[str, dict[str, Any]],
    prior_documents: list[dict[str, Any]],
    prior_sections: list[dict[str, Any]],
    packet_tokens: dict[str, set[str]],
    topology_index: dict[str, Any],
    policy: dict[str, Any],
    first_seen_by_paragraph: dict[str, str],
) -> list[dict[str, Any]]:
    components_cfg = dict((policy.get("scoring") or {}).get("components") or {})
    qualification_cfg = dict(policy.get("qualification") or {})
    glossary_penalty_value = float(
        (policy.get("scoring") or {}).get("glossary_terminal_penalty", 0.0) or 0.0
    )
    component_evidence_min = float(qualification_cfg.get("component_evidence_min", 0.0) or 0.0)
    total_score_min = float(qualification_cfg.get("total_score_min", 0.0) or 0.0)
    candidates_by_id: dict[str, dict[str, Any]] = {}
    selected_mode_order = tuple(mode for mode in MODE_ORDER if mode in mode_results)

    for mode in selected_mode_order:
        result = mode_results[mode]
        for index, row in enumerate(list(result.get("rows") or []), start=1):
            identity = _candidate_identity(row)
            paragraph_id = identity["paragraph_id"]
            if not paragraph_id:
                continue
            candidate = candidates_by_id.get(paragraph_id)
            if candidate is None:
                first_seen_stage = first_seen_by_paragraph.setdefault(paragraph_id, stage)
                candidate = {
                    "paragraph_id": paragraph_id,
                    "first_seen_stage": first_seen_stage,
                    "retrieval_stage": stage,
                    "seen_in_modes": [],
                    "mode_row_refs": [],
                    "rows_by_mode": {},
                }
                candidates_by_id[paragraph_id] = candidate
            candidate["rows_by_mode"][mode] = dict(row)
            if mode not in candidate["seen_in_modes"]:
                candidate["seen_in_modes"].append(mode)
            candidate["mode_row_refs"].append(
                _mode_row_ref(mode=mode, stage=stage, row=row, rank=index)
            )

    ranked_candidates: list[dict[str, Any]] = []
    for candidate in candidates_by_id.values():
        canonical_row, canonical_merge = _merge_canonical_row(dict(candidate["rows_by_mode"]))
        identity = _candidate_identity(canonical_row)
        defined_rows = _parse_role_payload(canonical_row.get("defined_terms_json"))
        term_ref_rows = _parse_role_payload(canonical_row.get("term_refs_json"))
        syntax_rows = _parse_role_payload(
            canonical_row.get("syntax_defs_json")
        ) + _parse_role_payload(canonical_row.get("syntax_refs_json"))
        std_rows = _parse_role_payload(canonical_row.get("std_refs_json"))
        defined_score, defined_matches = _role_match_score(
            defined_rows,
            packet_terms=packet_tokens["text_tokens"],
        )
        term_ref_score, term_ref_matches = _role_match_score(
            term_ref_rows,
            packet_terms=packet_tokens["text_tokens"],
        )
        syntax_score, syntax_matches = _role_match_score(
            syntax_rows,
            packet_terms=set().union(
                packet_tokens["construct_terms"], packet_tokens["code_tokens"]
            ),
        )
        std_score, std_matches = _role_match_score(
            std_rows,
            packet_terms=set().union(
                packet_tokens["construct_terms"], packet_tokens["code_tokens"]
            ),
        )
        code_score, code_matches = _code_evidence_score(
            canonical_row,
            packet_code_terms=set().union(
                packet_tokens["construct_terms"], packet_tokens["code_tokens"]
            ),
        )
        phrase_evidence = _phrase_evidence_score(
            canonical_row,
            {"supporting_phrases": list(packet_tokens.get("supporting_phrase_texts", []))},
        )
        content_type = _candidate_content_type(canonical_row)
        glossary_candidate = _is_glossary_candidate(canonical_row)
        score_components = {
            "text_overlap_score": _overlap_score(
                str(canonical_row.get("chunk_text", canonical_row.get("text", ""))), packet_tokens
            ),
            "phrase_evidence_score": float(
                phrase_evidence.get("phrase_evidence_score", 0.0) or 0.0
            ),
            "document_prior_score": _prior_score(
                prior_documents,
                key="document_link",
                value=str(canonical_row.get("document_link", "")),
                stage=stage,
            ),
            "section_prior_score": _prior_score(
                prior_sections,
                key="section_link",
                value=str(canonical_row.get("section_link", "")),
                stage=stage,
            ),
            "defined_term_match_score": defined_score,
            "term_ref_match_score": term_ref_score,
            "syntax_match_score": syntax_score,
            "std_ref_match_score": std_score,
            "code_evidence_score": code_score,
            "glossary_terminal_penalty": (
                -round(glossary_penalty_value, 6)
                if glossary_candidate and glossary_penalty_value > 0
                else 0.0
            ),
            "ambiguity_penalty": 0.0,
        }
        bonus_total = 0.0
        for name, value in score_components.items():
            component_cfg = dict(components_cfg.get(name) or {})
            weight = float(component_cfg.get("weight", 1.0) or 0.0)
            if name in {"glossary_terminal_penalty", "ambiguity_penalty"}:
                continue
            bonus_total += weight * float(value)
        total_score = bonus_total + float(score_components["glossary_terminal_penalty"])
        topology_row = get_paragraph(topology_index, str(identity["paragraph_id"]))
        non_prior_evidence = max(
            float(score_components["text_overlap_score"]),
            float(score_components["defined_term_match_score"]),
            float(score_components["term_ref_match_score"]),
            float(score_components["syntax_match_score"]),
            float(score_components["std_ref_match_score"]),
            float(score_components["code_evidence_score"]),
        )
        structurally_eligible = all(
            (
                bool(identity["chunk_uid"]),
                bool(identity["paragraph_id"]),
                bool(identity["paragraph_link"]),
                topology_row is not None,
                not canonical_merge["identity_conflicts"],
                non_prior_evidence >= component_evidence_min,
                round(total_score, 6) >= total_score_min,
                all(name in score_components for name in components_cfg),
            )
        )
        qualifying = structurally_eligible and _candidate_meets_gate_floor(
            {
                "total_score": round(total_score, 6),
            },
            policy=policy,
        )
        candidate.update(
            {
                "chunk_uid": identity["chunk_uid"],
                "paragraph_link": identity["paragraph_link"],
                "document_link": str(canonical_row.get("document_link", "")).strip(),
                "section_link": str(canonical_row.get("section_link", "")).strip(),
                "chapter": str(canonical_row.get("chapter", "")).strip(),
                "section": str(
                    canonical_row.get("section", canonical_row.get("section_heading", ""))
                ).strip(),
                "paragraph_number": str(canonical_row.get("paragraph_number", "")).strip(),
                "text": str(
                    canonical_row.get(
                        "text",
                        canonical_row.get("statement_text", canonical_row.get("chunk_text", "")),
                    )
                ),
                "chunk_text": str(
                    canonical_row.get("chunk_text", canonical_row.get("statement_text", ""))
                ),
                "source_anchor": str(canonical_row.get("source_anchor", "")).strip(),
                "checksum": str(canonical_row.get("checksum", "")).strip(),
                "canonical_row": canonical_row,
                "canonical_merge": canonical_merge,
                "content_type": content_type,
                "glossary_candidate": glossary_candidate,
                "phrase_evidence": phrase_evidence,
                "matched_role_features": {
                    "defined_terms": defined_matches,
                    "term_refs": term_ref_matches,
                    "syntax": syntax_matches,
                    "std_refs": std_matches,
                },
                "matched_code_terms": code_matches,
                "score_components": score_components,
                "base_score": round(bonus_total, 6),
                "total_score": round(total_score, 6),
                "structurally_eligible_candidate": structurally_eligible,
                "qualifying_candidate": qualifying,
            }
        )
        ranked_candidates.append(candidate)

    ranked_candidates.sort(
        key=lambda row: (
            -float(row.get("total_score", 0.0)),
            -float(row.get("score_components", {}).get("text_overlap_score", 0.0)),
            str(row.get("paragraph_id", "")),
        )
    )
    if ranked_candidates:
        ambiguity_floor = float(
            (policy.get("scoring") or {}).get("ambiguity_margin_floor", 0.0) or 0.0
        )
        ambiguity_penalty_max = float(
            (policy.get("scoring") or {}).get("ambiguity_penalty_max", 0.0) or 0.0
        )
        top_candidate = ranked_candidates[0]
        runner_up = ranked_candidates[1] if len(ranked_candidates) > 1 else None
        margin = (
            float(top_candidate.get("total_score", 0.0)) - float(runner_up.get("total_score", 0.0))
            if runner_up is not None
            else 1.0
        )
        top_candidate["ambiguity_margin"] = round(margin, 6)
        if margin < ambiguity_floor and ambiguity_floor > 0.0 and ambiguity_penalty_max > 0.0:
            penalty = -round(min(ambiguity_penalty_max, ambiguity_floor - margin), 6)
            top_candidate["score_components"]["ambiguity_penalty"] = penalty
    return ranked_candidates


def _resolve_stage_scope(
    *,
    stage: str,
    packet: dict[str, Any],
    topology_index: dict[str, Any],
) -> tuple[list[str] | None, dict[str, Any]]:
    if stage == "global":
        return None, {"state": "global", "allowed_paragraph_ids": [], "source_links": []}

    if stage == "section":
        prior_rows = list(packet.get("prior_sections") or [])
        key = "section_link"
        resolver = paragraph_ids_for_section
    else:
        prior_rows = list(packet.get("prior_documents") or [])
        key = "document_link"
        resolver = paragraph_ids_for_document

    source_links: list[str] = []
    paragraph_ids: list[str] = []
    seen_ids: set[str] = set()
    for row in prior_rows:
        if not isinstance(row, dict):
            continue
        link = str(row.get(key, "")).strip()
        if not link:
            continue
        source_links.append(link)
        for paragraph_id in resolver(topology_index, link):
            if paragraph_id in seen_ids:
                continue
            seen_ids.add(paragraph_id)
            paragraph_ids.append(paragraph_id)
    state = "restricted_subset" if paragraph_ids else "restricted_empty"
    return paragraph_ids, {
        "state": state,
        "allowed_paragraph_ids": paragraph_ids,
        "source_links": source_links,
    }


def _scope_info(packet: dict[str, Any], stage_name: str, source_links: list[str]) -> dict[str, Any]:
    if stage_name == "global":
        info = {
            "state": "global",
            "scope_kind": "global",
            "source_links": [],
            "specificity_state": "global",
            "specificity_reasons": [],
            "content_type_mix": {
                "normative": 0,
                "glossary": 0,
                "inventory": 0,
                "index": 0,
                "examples": 0,
                "unknown": 0,
            },
            "glossary_share": 0.0,
            "hub_share": 0.0,
            "normative_share": 0.0,
            "selected_prior_count": 0,
        }
        info["terminal_confidence_allowed"] = True
        return info
    scope_key = "prior_sections" if stage_name == "section" else "prior_documents"
    rows = [
        row
        for row in list(packet.get(scope_key) or [])
        if isinstance(row, dict)
        and str(row.get("section_link" if stage_name == "section" else "document_link", ""))
        in set(source_links)
    ]
    first = rows[0] if rows else {}
    content_type_mix = {
        "normative": 0,
        "glossary": 0,
        "inventory": 0,
        "index": 0,
        "examples": 0,
        "unknown": 0,
    }
    for row in rows:
        content_type = str(row.get("content_type", "unknown") or "unknown")
        content_type_mix[content_type if content_type in content_type_mix else "unknown"] += 1
    selected_prior_count = len(rows)
    glossary_share = content_type_mix["glossary"] / max(1, selected_prior_count)
    hub_share = (
        content_type_mix["glossary"] + content_type_mix["inventory"] + content_type_mix["index"]
    ) / max(1, selected_prior_count)
    normative_share = content_type_mix["normative"] / max(1, selected_prior_count)
    info = {
        "state": "restricted_subset" if selected_prior_count else "restricted_empty",
        "scope_kind": stage_name,
        "source_links": list(source_links),
        "specificity_state": str(
            first.get("specificity_state", "high_specificity") or "high_specificity"
        ),
        "specificity_reasons": list(
            (first.get("evidence") or {})
            .get("prior_health_snapshot", {})
            .get("specificity_reasons")
            or []
        ),
        "content_type_mix": content_type_mix,
        "glossary_share": round(glossary_share, 6),
        "hub_share": round(hub_share, 6),
        "normative_share": round(normative_share, 6),
        "selected_prior_count": selected_prior_count,
    }
    info["terminal_confidence_allowed"] = _terminal_confidence_allowed(info)
    return info


def _terminal_confidence_allowed(scope_info: dict[str, Any]) -> bool:
    if str(scope_info.get("scope_kind", "")) == "global":
        return True
    return str(scope_info.get("specificity_state", "")) == "high_specificity"


def _stage_can_terminate(
    stage_name: str,
    scope_info: dict[str, Any],
    winning_candidate: dict[str, Any] | None,
    decision_inputs: dict[str, Any],
) -> tuple[bool, str]:
    if winning_candidate is None:
        if stage_name == "global":
            return False, REASON_NO_QUALIFYING
        return False, REASON_SCOPED_NO_QUALIFYING
    if stage_name == "global":
        return True, REASON_GLOBAL_STAGE
    if bool(scope_info.get("terminal_confidence_allowed", False)):
        return True, REASON_TERMINAL_STAGE_SUCCESS
    if bool(winning_candidate.get("glossary_candidate", False)):
        return False, REASON_SCOPED_GLOSSARY
    phrase_score = float(
        winning_candidate.get("phrase_evidence", {}).get("phrase_evidence_score", 0.0) or 0.0
    )
    margin = float(decision_inputs.get("margin", 0.0) or 0.0)
    stage_health_cfg = dict(decision_inputs.get("stage_health_cfg") or {})
    required_margin = float(
        stage_health_cfg.get("scoped_nonterminal_override_margin", 0.14) or 0.14
    )
    if stage_name == "document":
        required_margin -= float(
            stage_health_cfg.get("document_stage_override_margin_delta", 0.02) or 0.02
        )
    strong_phrase_threshold = float(stage_health_cfg.get("strong_phrase_threshold", 0.55) or 0.55)
    if phrase_score < strong_phrase_threshold:
        return False, REASON_SCOPED_WEAK_PHRASE
    if margin < required_margin:
        return False, REASON_SCOPED_WEAK_SCOPE
    return True, REASON_SCOPED_OVERRIDE


def _run_stage(
    *,
    project_root: Path,
    stage: str,
    packet: dict[str, Any],
    topology_index: dict[str, Any],
    db_path: Path,
    runtime_settings: dict[str, Any],
    policy: dict[str, Any],
    first_seen_by_paragraph: dict[str, str],
) -> dict[str, Any]:
    execute_retrieval_query, SemanticBackendConfig = _load_runtime_components(project_root)
    allowed_ids, scope_info = _resolve_stage_scope(
        stage=stage, packet=packet, topology_index=topology_index
    )
    scope_metadata = _scope_info(packet, stage, list(scope_info["source_links"]))
    query_text = _build_query_text(project_root, packet)
    _trace_event(
        policy,
        {
            "event": "stage_start",
            "stage": stage,
            "scope_state": scope_info["state"],
            "source_links": list(scope_info["source_links"]),
            "allowed_paragraph_count": len(scope_info["allowed_paragraph_ids"]),
            "scope_info": scope_metadata,
        },
    )
    semantic_config = SemanticBackendConfig(
        base_url=str(runtime_settings["semantic_base_url"]),
        embed_base_url=str(runtime_settings["semantic_embed_base_url"]),
        rerank_base_url=str(runtime_settings["semantic_rerank_base_url"]),
        embed_model_id=str(runtime_settings["embed_model_id"]),
        reranker_model_id=str(runtime_settings["reranker_model_id"]),
        timeout_sec=float(runtime_settings["semantic_timeout_sec"]),
    )
    selected_mode_order = tuple(
        mode
        for mode in list(runtime_settings.get("ws7_modes") or list(MODE_ORDER))
        if mode in MODE_ORDER
    )
    if not selected_mode_order:
        selected_mode_order = MODE_ORDER

    def run_mode_queries(*, allowed_statement_ids: list[str] | None) -> dict[str, dict[str, Any]]:
        local_results: dict[str, dict[str, Any]] = {}
        rewrite_mode = (
            str(((policy.get("query") or {}).get("rewrite_mode", "off"))).strip() or "off"
        )
        for mode in selected_mode_order:
            local_results[mode] = execute_retrieval_query(
                mode=mode,
                db_path=db_path,
                contract_path=Path(runtime_settings["contract_path"]),
                query_log_root=Path(runtime_settings["query_log_root"]),
                query_text=query_text,
                row_marker="",
                top_k=int((policy.get("query") or {}).get("top_k", runtime_settings["top_k"])),
                candidate_limit=int(
                    (policy.get("query") or {}).get(
                        "candidate_limit", runtime_settings["candidate_limit"]
                    )
                ),
                allow_degraded=False,
                semantic_config=semantic_config,
                semantic_retries=int(runtime_settings["semantic_retries"]),
                persist_semantic_cache=False,
                allow_online_corpus_embedding=False,
                rewrite_mode=rewrite_mode,
                rewrite_rules_path=Path(runtime_settings["rewrite_rules_path"]),
                hybrid_fusion_method=str(runtime_settings["hybrid_fusion_method"]),
                hybrid_rrf_k=int(runtime_settings["hybrid_rrf_k"]),
                hybrid_rrf_window=int(runtime_settings["hybrid_rrf_window"]),
                hybrid_lexical_floor_count=int(runtime_settings["hybrid_lexical_floor_count"]),
                hybrid_lexical_floor_share=float(runtime_settings["hybrid_lexical_floor_share"]),
                hybrid_candidate_policy=str(runtime_settings["hybrid_candidate_policy"]),
                hybrid_rerank_pool_size=int(runtime_settings["hybrid_rerank_pool_size"]),
                hybrid_lexical_min=int(runtime_settings["hybrid_lexical_min"]),
                hybrid_semantic_min=int(runtime_settings["hybrid_semantic_min"]),
                corpus="fls_spec",
                allowed_statement_ids=allowed_statement_ids,
            )
        return local_results

    mode_results = run_mode_queries(allowed_statement_ids=allowed_ids)

    packet_tokens = _tokenize_packet(packet)
    stage_candidates = _merge_stage_candidates(
        stage=stage,
        mode_results=mode_results,
        prior_documents=list(packet.get("prior_documents") or []),
        prior_sections=list(packet.get("prior_sections") or []),
        packet_tokens=packet_tokens,
        topology_index=topology_index,
        policy=policy,
        first_seen_by_paragraph=first_seen_by_paragraph,
    )
    qualifying_candidates = [
        row for row in stage_candidates if bool(row.get("qualifying_candidate", False))
    ]
    qualifying_by_mode = {
        mode: sum(
            1 for row in qualifying_candidates if mode in list(row.get("seen_in_modes") or [])
        )
        for mode in selected_mode_order
    }
    advancement_reason = ADVANCEMENT_NO_QUALIFYING
    if qualifying_candidates:
        advancement_reason = ADVANCEMENT_STAGE_SUCCESS
    elif stage != "global":
        advancement_reason = ADVANCEMENT_GLOBAL_FALLBACK
    _trace_event(
        policy,
        {
            "event": "stage_end",
            "stage": stage,
            "advancement_reason": advancement_reason,
            "candidate_count": len(stage_candidates),
            "qualifying_candidate_count": len(qualifying_candidates),
            "mode_returned_counts": {
                mode: len(list(mode_results[mode].get("rows") or []))
                for mode in selected_mode_order
            },
            "mode_qualifying_counts": qualifying_by_mode,
            "top_candidates": [
                {
                    "paragraph_id": str(row.get("paragraph_id", "")),
                    "total_score": float(row.get("total_score", 0.0) or 0.0),
                    "glossary_candidate": bool(row.get("glossary_candidate", False)),
                    "phrase_evidence_score": float(
                        row.get("phrase_evidence", {}).get("phrase_evidence_score", 0.0) or 0.0
                    ),
                }
                for row in stage_candidates[:3]
            ],
            "scope_info": scope_metadata,
            "selected_modes": list(selected_mode_order),
        },
    )
    return {
        "stage_name": stage,
        "query_text": query_text,
        "scope": {
            **scope_info,
            "scope_info": scope_metadata,
            "candidate_universe_size": (
                len(scope_info["allowed_paragraph_ids"])
                if scope_info["state"] != "global"
                else None
            ),
        },
        "mode_results": mode_results,
        "candidates": stage_candidates,
        "qualifying_candidates": qualifying_candidates,
        "candidate_universe_size": (
            len(scope_info["allowed_paragraph_ids"]) if scope_info["state"] != "global" else -1
        ),
        "entered_with_priors": bool(scope_info["source_links"]),
        "scope_info": scope_metadata,
        "advancement_reason": advancement_reason,
        "mode_qualifying_counts": qualifying_by_mode,
        "stage_artifact": {
            "stage_name": stage,
            "mode_artifacts": {
                mode: {
                    **_artifact_mode_ref(
                        stage=stage,
                        query_text=query_text,
                        mode=mode,
                        result=mode_results[mode],
                        qualifying_count=qualifying_by_mode[mode],
                    ),
                    "retrieval_result_ref": {
                        **_artifact_mode_ref(
                            stage=stage,
                            query_text=query_text,
                            mode=mode,
                            result=mode_results[mode],
                            qualifying_count=qualifying_by_mode[mode],
                        )["retrieval_result_ref"],
                        "qualifying_paragraph_ids": [
                            str(row.get("paragraph_id", ""))
                            for row in qualifying_candidates
                            if mode in list(row.get("seen_in_modes") or [])
                            and str(row.get("paragraph_id", ""))
                        ],
                    },
                }
                for mode in selected_mode_order
            },
            "candidate_universe_size": (
                len(scope_info["allowed_paragraph_ids"]) if scope_info["state"] != "global" else -1
            ),
            "advancement_reason": advancement_reason,
            "candidate_ids": _candidate_ids(stage_candidates),
        },
    }


def _select_stage_winner(
    stage_result: dict[str, Any], *, policy: dict[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    gating_cfg = dict(policy.get("gating") or {})
    stage_health_cfg = dict(policy.get("stage_health") or {})
    stage_name = str(stage_result.get("stage_name", "global") or "global")
    scope_info = dict(stage_result.get("scope_info") or {})
    if not scope_info:
        scope_info = _scope_info({}, stage_name, [])
    candidates = list(stage_result.get("qualifying_candidates") or [])
    if not candidates:
        reason_code = (
            REASON_NO_QUALIFYING if stage_name == "global" else REASON_SCOPED_NO_QUALIFYING
        )
        _trace_event(
            policy,
            {
                "event": "stage_decision",
                "stage": stage_name,
                "reason_code": reason_code,
                "accepted": False,
                "review_candidate": False,
            },
        )
        return None, {
            "reason_code": reason_code,
            "publish_accept": False,
            "review_candidate": False,
            "accepted": False,
            "glossary_override_applied": False,
            "glossary_ladder_state": "not_glossary_candidate",
            "glossary_acceptance_allowed": False,
            "glossary_acceptance_reason": "not_glossary_candidate",
        }
    candidates.sort(
        key=lambda row: (-float(row.get("total_score", 0.0)), str(row.get("paragraph_id", "")))
    )
    winner = candidates[0]
    runner_up = candidates[1] if len(candidates) > 1 else None
    comparison_margin = float((policy.get("scoring") or {}).get("comparison_margin", 0.0) or 0.0)
    acceptance_total_min = float(gating_cfg.get("acceptance_total_score_min", 0.0) or 0.0)
    acceptance_margin_min = float(gating_cfg.get("acceptance_margin_min", comparison_margin) or 0.0)
    review_total_min = float(gating_cfg.get("review_total_score_min", 0.0) or 0.0)
    glossary_override_applied = False
    if bool(winner.get("glossary_candidate", False)) and comparison_margin > 0.0:
        for candidate in candidates[1:]:
            if bool(candidate.get("glossary_candidate", False)):
                continue
            if (
                float(winner.get("total_score", 0.0)) - float(candidate.get("total_score", 0.0))
                <= comparison_margin
            ):
                winner = candidate
                glossary_override_applied = True
                break

    comparison_candidates = list(candidates)
    if glossary_override_applied and not bool(winner.get("glossary_candidate", False)):
        comparison_candidates = [
            candidate
            for candidate in comparison_candidates
            if not bool(candidate.get("glossary_candidate", False))
        ]
        if not comparison_candidates:
            comparison_candidates = [winner]

    resorted = sorted(
        comparison_candidates,
        key=lambda row: (
            row.get("paragraph_id", "") != winner.get("paragraph_id", ""),
            -float(row.get("total_score", 0.0)),
            str(row.get("paragraph_id", "")),
        ),
    )
    top = resorted[0]
    runner_up = resorted[1] if len(resorted) > 1 else None
    margin = (
        float(top.get("total_score", 0.0)) - float(runner_up.get("total_score", 0.0))
        if runner_up is not None
        else 1.0
    )
    top["decision_margin"] = round(margin, 6)
    can_terminate, termination_reason = _stage_can_terminate(
        stage_name,
        scope_info,
        top,
        {"margin": margin, "stage_health_cfg": stage_health_cfg},
    )
    glossary_candidate = bool(top.get("glossary_candidate", False))
    glossary_ladder_state = "not_glossary_candidate"
    glossary_acceptance_allowed = not glossary_candidate
    glossary_acceptance_reason = "not_glossary_candidate"
    if glossary_candidate:
        glossary_ladder_state = "glossary_provisional"
        glossary_acceptance_allowed = False
        if (
            stage_name != "global"
            and bool(gating_cfg.get("glossary_scoped_acceptance_forbidden_when_dominated", True))
            and str(scope_info.get("specificity_state", ""))
            in {"low_specificity", "glossary_dominated"}
        ):
            glossary_acceptance_reason = "glossary_dominated_stage_forbids_glossary_acceptance"
        elif stage_name != "global":
            glossary_acceptance_reason = "low_specificity_glossary_requires_review"
        elif float(top.get("phrase_evidence", {}).get("phrase_evidence_score", 0.0) or 0.0) < float(
            stage_health_cfg.get("strong_phrase_threshold", 0.55) or 0.55
        ):
            glossary_acceptance_reason = "glossary_thresholds_not_met"
        else:
            glossary_acceptance_allowed = True
            glossary_acceptance_reason = "global_glossary_acceptance_allowed"

    if (
        float(top.get("total_score", 0.0)) >= acceptance_total_min
        and margin >= acceptance_margin_min
        and can_terminate
        and (
            not glossary_candidate
            or (
                glossary_acceptance_allowed
                and float(top.get("total_score", 0.0))
                >= float(gating_cfg.get("glossary_acceptance_total_score_min", 0.42) or 0.42)
                and margin >= float(gating_cfg.get("glossary_acceptance_margin_min", 0.12) or 0.12)
            )
        )
    ):
        _trace_event(
            policy,
            {
                "event": "stage_decision",
                "stage": stage_name,
                "reason_code": termination_reason
                if termination_reason != REASON_TERMINAL_STAGE_SUCCESS
                else REASON_ACCEPTED,
                "accepted": True,
                "review_candidate": False,
                "paragraph_id": str(top.get("paragraph_id", "")),
                "total_score": float(top.get("total_score", 0.0) or 0.0),
                "decision_margin": float(top.get("decision_margin", 0.0) or 0.0),
                "glossary_override_applied": glossary_override_applied,
                "glossary_acceptance_reason": glossary_acceptance_reason,
            },
        )
        return top, {
            "reason_code": REASON_ACCEPTED,
            "publish_accept": True,
            "review_candidate": False,
            "accepted": True,
            "glossary_override_applied": glossary_override_applied,
            "glossary_ladder_state": "glossary_accepted"
            if glossary_candidate
            else glossary_ladder_state,
            "glossary_acceptance_allowed": glossary_acceptance_allowed,
            "glossary_acceptance_reason": glossary_acceptance_reason,
            "stage_terminal": True,
        }

    if not can_terminate and stage_name != "global":
        review_candidate = float(top.get("total_score", 0.0) or 0.0) >= review_total_min
        _trace_event(
            policy,
            {
                "event": "stage_decision",
                "stage": stage_name,
                "reason_code": termination_reason,
                "accepted": False,
                "review_candidate": review_candidate,
                "paragraph_id": str(top.get("paragraph_id", "")),
                "total_score": float(top.get("total_score", 0.0) or 0.0),
                "decision_margin": float(top.get("decision_margin", 0.0) or 0.0),
                "glossary_override_applied": glossary_override_applied,
                "glossary_acceptance_reason": glossary_acceptance_reason,
            },
        )
        return top, {
            "reason_code": termination_reason,
            "publish_accept": False,
            "review_candidate": review_candidate,
            "accepted": False,
            "glossary_override_applied": glossary_override_applied,
            "glossary_ladder_state": glossary_ladder_state,
            "glossary_acceptance_allowed": glossary_acceptance_allowed,
            "glossary_acceptance_reason": glossary_acceptance_reason,
            "stage_terminal": False,
        }

    if float(top.get("total_score", 0.0)) >= review_total_min:
        reason_code = REASON_AMBIGUOUS if margin < acceptance_margin_min else REASON_REVIEW
        _trace_event(
            policy,
            {
                "event": "stage_decision",
                "stage": stage_name,
                "reason_code": reason_code,
                "accepted": False,
                "review_candidate": True,
                "paragraph_id": str(top.get("paragraph_id", "")),
                "total_score": float(top.get("total_score", 0.0) or 0.0),
                "decision_margin": float(top.get("decision_margin", 0.0) or 0.0),
                "glossary_override_applied": glossary_override_applied,
                "glossary_acceptance_reason": glossary_acceptance_reason,
            },
        )
        return top, {
            "reason_code": reason_code,
            "publish_accept": False,
            "review_candidate": True,
            "accepted": False,
            "glossary_override_applied": glossary_override_applied,
            "glossary_ladder_state": glossary_ladder_state,
            "glossary_acceptance_allowed": glossary_acceptance_allowed,
            "glossary_acceptance_reason": glossary_acceptance_reason,
            "stage_terminal": stage_name == "global",
        }

    _trace_event(
        policy,
        {
            "event": "stage_decision",
            "stage": stage_name,
            "reason_code": REASON_NO_QUALIFYING,
            "accepted": False,
            "review_candidate": False,
            "glossary_override_applied": glossary_override_applied,
            "glossary_acceptance_reason": glossary_acceptance_reason,
        },
    )
    return None, {
        "reason_code": REASON_NO_QUALIFYING,
        "publish_accept": False,
        "review_candidate": False,
        "accepted": False,
        "glossary_override_applied": glossary_override_applied,
        "glossary_ladder_state": glossary_ladder_state,
        "glossary_acceptance_allowed": glossary_acceptance_allowed,
        "glossary_acceptance_reason": glossary_acceptance_reason,
        "stage_terminal": False,
    }


def resolve_guideline(
    *,
    project_root: Path,
    packet: dict[str, Any],
    db_path: Path,
    runtime_settings: dict[str, Any],
    topology_index: dict[str, Any],
    policy_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = _load_policy(project_root, overrides=policy_overrides)
    runtime_packet = _project_query_inputs(packet)
    validation_only_continuation = bool(
        (policy_overrides or {}).get("validation_only_continuation", False)
    )
    first_seen_by_paragraph: dict[str, str] = {}
    stage_results: list[dict[str, Any]] = []
    stage_artifacts: list[dict[str, Any]] = []
    selected_stage = ""
    selected_candidate: dict[str, Any] | None = None
    provisional_candidate: dict[str, Any] | None = None
    provisional_decision: dict[str, Any] | None = None
    provisional_stage = ""
    decision: dict[str, Any] = {
        "reason_code": REASON_NO_QUALIFYING,
        "publish_accept": False,
        "review_candidate": False,
        "accepted": False,
        "glossary_override_applied": False,
    }

    for stage in STAGE_ORDER:
        stage_result = _run_stage(
            project_root=project_root,
            stage=stage,
            packet=runtime_packet,
            topology_index=topology_index,
            db_path=db_path,
            runtime_settings=runtime_settings,
            policy=policy,
            first_seen_by_paragraph=first_seen_by_paragraph,
        )
        stage_results.append(stage_result)
        stage_artifact = dict(stage_result["stage_artifact"])
        candidate, stage_decision = _select_stage_winner(stage_result, policy=policy)
        if not bool(stage_decision.get("stage_terminal", False)) and stage != STAGE_ORDER[-1]:
            stage_artifact["advancement_reason"] = ADVANCEMENT_GLOBAL_FALLBACK
        if validation_only_continuation and candidate is not None and stage != STAGE_ORDER[-1]:
            stage_artifact["advancement_reason"] = ADVANCEMENT_VALIDATION_ONLY
        stage_artifacts.append(stage_artifact)
        if candidate is not None:
            if bool(stage_decision.get("accepted", False)):
                if selected_candidate is None:
                    decision.update(stage_decision)
                    selected_stage = stage
                    selected_candidate = candidate
                if not validation_only_continuation:
                    break
            elif bool(stage_decision.get("review_candidate", False)):
                if provisional_candidate is None:
                    provisional_candidate = candidate
                    provisional_decision = dict(stage_decision)
                    provisional_stage = stage
            if not validation_only_continuation and bool(stage_decision.get("accepted", False)):
                break

    if selected_candidate is None and provisional_candidate is not None:
        selected_candidate = provisional_candidate
        selected_stage = provisional_stage
        decision.update(provisional_decision or {})

    _trace_event(
        policy,
        {
            "event": "resolution_complete",
            "selected_stage": selected_stage,
            "selected_paragraph_id": str((selected_candidate or {}).get("paragraph_id", "")),
            "reason_code": str(decision.get("reason_code", "")),
            "accepted": bool(decision.get("accepted", False)),
            "review_candidate": bool(decision.get("review_candidate", False)),
            "stage_count": len(stage_artifacts),
        },
    )

    if selected_candidate is None:
        return {
            "paragraph_id": "fls_UNRESOLVED",
            "text": "",
            "chapter": "",
            "section": "",
            "paragraph_number": "",
            "unresolved_reason": "no qualifying staged candidate found",
            "decision": {
                **decision,
                "runtime_mode": "ws7_staged_retrieval_v1",
                "grounding_only_runtime": False,
                "selected_stage": "",
                "top_candidates": [],
                "stage_artifacts": stage_artifacts,
            },
        }

    selected_stage_result = next(
        result for result in stage_results if result["stage_name"] == selected_stage
    )
    top_candidates = []
    for row in list(selected_stage_result.get("candidates") or [])[:10]:
        top_candidates.append(
            {
                "chunk_uid": str(row.get("chunk_uid", "")),
                "paragraph_id": str(row.get("paragraph_id", "")),
                "paragraph_link": str(row.get("paragraph_link", "")),
                "retrieval_stage": str(row.get("retrieval_stage", "")),
                "first_seen_stage": str(row.get("first_seen_stage", "")),
                "seen_in_modes": list(row.get("seen_in_modes") or []),
                "mode_row_refs": {
                    str(ref.get("mode", "")): {
                        "rank": int(ref.get("rank", 0) or 0),
                        "stage": str(ref.get("stage", "")),
                        "chunk_uid": str(ref.get("chunk_uid", "")),
                        "paragraph_id": str(ref.get("paragraph_id", "")),
                        "relevance_score": float(ref.get("relevance_score", 0.0) or 0.0),
                        "lexical_score": float(ref.get("lexical_score", 0.0) or 0.0),
                        "semantic_score": float(ref.get("semantic_score", 0.0) or 0.0),
                        "reranker_score": float(ref.get("reranker_score", 0.0) or 0.0),
                    }
                    for ref in list(row.get("mode_row_refs") or [])
                },
                "score_components": dict(row.get("score_components") or {}),
                "total_score": float(row.get("total_score", 0.0) or 0.0),
                "glossary_candidate": bool(row.get("glossary_candidate", False)),
                "matched_role_features": dict(row.get("matched_role_features") or {}),
                "canonical_merge": dict(row.get("canonical_merge") or {}),
                "qualifying_candidate": bool(row.get("qualifying_candidate", False)),
            }
        )

    return {
        "paragraph_id": str(selected_candidate.get("paragraph_id", "")),
        "text": str(selected_candidate.get("text", "")),
        "chapter": str(selected_candidate.get("chapter", "")),
        "section": str(selected_candidate.get("section", "")),
        "paragraph_number": str(selected_candidate.get("paragraph_number", "")),
        "paragraph_link": str(selected_candidate.get("paragraph_link", "")),
        "document_link": str(selected_candidate.get("document_link", "")),
        "section_link": str(selected_candidate.get("section_link", "")),
        "chunk_uid": str(selected_candidate.get("chunk_uid", "")),
        "decision": {
            **decision,
            "runtime_mode": "ws7_staged_retrieval_v1",
            "grounding_only_runtime": False,
            "selected_stage": selected_stage,
            "selected_candidate": {
                "paragraph_id": str(selected_candidate.get("paragraph_id", "")),
                "chunk_uid": str(selected_candidate.get("chunk_uid", "")),
                "paragraph_link": str(selected_candidate.get("paragraph_link", "")),
                "total_score": float(selected_candidate.get("total_score", 0.0) or 0.0),
                "score_components": dict(selected_candidate.get("score_components") or {}),
                "canonical_merge": dict(selected_candidate.get("canonical_merge") or {}),
            },
            "top_candidates": top_candidates,
            "stage_artifacts": stage_artifacts,
        },
    }
