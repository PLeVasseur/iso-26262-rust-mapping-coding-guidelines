from __future__ import annotations

import json
import re
import sqlite3
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
STAGE_ORDER = ("section", "document", "global")
MODE_ORDER = ("lexical", "semantic", "hybrid")
ADVANCEMENT_NO_QUALIFYING = "NO_QUALIFYING_CANDIDATES"
ADVANCEMENT_STAGE_SUCCESS = "TERMINAL_STAGE_SUCCESS"
ADVANCEMENT_GLOBAL_FALLBACK = "GLOBAL_FALLBACK_REQUIRED"
ADVANCEMENT_VALIDATION_ONLY = "VALIDATION_ONLY_CONTINUATION"
REASON_ACCEPTED = "ACCEPTED"
REASON_REVIEW = "REVIEW_REQUIRED"
REASON_NO_QUALIFYING = "NO_QUALIFYING_CANDIDATES"
REASON_WEAK = "WEAK_CANDIDATE"
REASON_AMBIGUOUS = "AMBIGUOUS_TOP_CANDIDATES"


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


def _build_query_text(project_root: Path, packet: dict[str, Any]) -> str:
    del project_root
    pieces: list[str] = []
    obligation = str(packet.get("governing_obligation", "")).strip()
    if obligation:
        pieces.append(obligation)
    construct_terms = [
        str(value).strip()
        for value in list(packet.get("construct_terms") or [])
        if str(value).strip()
    ]
    if construct_terms:
        pieces.append(" ".join(construct_terms[:10]))
    supporting_phrases = [
        str(value).strip()
        for value in list(packet.get("supporting_phrases") or [])
        if str(value).strip()
    ]
    if supporting_phrases:
        pieces.append(" ".join(supporting_phrases[:3]))
    lower_blob = " ".join(pieces).lower()
    if ("integer" in lower_blob or "numeric" in lower_blob) and "pointer" in lower_blob:
        pieces.append("address to pointer cast")
    if "transmute" in lower_blob:
        pieces.append("address to pointer cast")
    if "recursive" in lower_blob or "recursion" in lower_blob:
        pieces.append("recursive function stack overflow tail call")
    if "strong types" in lower_blob or "newtype" in lower_blob or "type alias" in lower_blob:
        pieces.append("distinct types type alias newtype")
    if "unsafe" in lower_blob and any(
        term in lower_blob
        for term in ("extern", "no_mangle", "export_name", "link_section", "attribute")
    ):
        pieces.append("unsafe attribute unsafe external block")
    merged = " ".join(piece for piece in pieces if piece)
    tokens = re.findall(r"[A-Za-z0-9_]+", merged)
    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        lowered = token.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(token)
        if len(deduped) >= 48:
            break
    return " ".join(deduped).strip()


def _tokenize_text(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(str(text or ""))}


def _tokenize_packet(packet: dict[str, Any]) -> dict[str, set[str]]:
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
    }


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
    query_tokens = packet_tokens["text_tokens"]
    if not query_tokens:
        return 0.0
    candidate_lower = str(candidate_text or "").lower()
    candidate_tokens = _tokenize_text(candidate_text)
    if not candidate_tokens:
        return 0.0
    overlap = len(candidate_tokens.intersection(query_tokens))
    score = overlap / max(1, len(query_tokens))
    if "unsafe" in query_tokens:
        score += 0.12 if "unsafe" in candidate_lower else -0.04
    if {"integer", "pointer"}.issubset(query_tokens) or {"numeric", "pointer"}.issubset(
        query_tokens
    ):
        if "address to pointer" in candidate_lower or (
            "integer type" in candidate_lower
            and ("*const" in candidate_lower or "*mut" in candidate_lower)
        ):
            score += 0.15
        if "function pointer" in candidate_lower and "function" not in query_tokens:
            score -= 0.12
    if (
        "recursive" in query_tokens
        and "type" in candidate_lower
        and "function" not in candidate_lower
    ):
        score -= 0.08
    if {"strong", "types"}.issubset(query_tokens) or {"distinct", "types"}.issubset(query_tokens):
        if "type alias" in candidate_lower:
            score += 0.10
        if "copy trait" in candidate_lower or "implements the core marker copy" in candidate_lower:
            score -= 0.08
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
    if not role_rows or not packet_terms:
        return 0.0, []
    matched: list[dict[str, str]] = []
    for row in role_rows:
        row_tokens = _tokenize_text(" ".join((row.get("text", ""), row.get("target", ""))))
        if row_tokens.intersection(packet_terms):
            matched.append(row)
    score = min(1.0, len(matched) / max(1, len(role_rows))) if matched else 0.0
    return round(float(score), 6), matched


def _is_glossary_candidate(row: dict[str, Any]) -> bool:
    document_link = str(row.get("document_link", "")).lower()
    section_link = str(row.get("section_link", "")).lower()
    section_heading = str(row.get("section_heading", row.get("section", ""))).lower()
    return (
        "glossary" in document_link or "glossary" in section_link or "glossary" in section_heading
    )


def _is_glossary_link(link: str) -> bool:
    lowered = str(link or "").strip().lower()
    return "glossary" in lowered


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
MERGEABLE_FIELDS = tuple(field for field in CANONICAL_FIELDS if field not in IDENTITY_FIELDS)


def _canonical_value_key(value: Any) -> tuple[int, str]:
    normalized = str(value or "").strip()
    return (len(normalized), normalized)


def _choose_field_value(rows_by_mode: dict[str, dict[str, Any]], field: str) -> Any:
    values = {
        str(row.get(field, "")).strip(): row.get(field)
        for row in rows_by_mode.values()
        if str(row.get(field, "")).strip()
    }
    if not values:
        return ""
    best_key = max(values, key=lambda item: _canonical_value_key(item))
    return values[best_key]


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
    selected_values: dict[str, str] = {}
    for field in IDENTITY_FIELDS:
        values = _identity_values(rows_by_mode, field)
        if len(values) > 1:
            identity_conflicts[field] = values
        selected = values[0] if values else ""
        canonical[field] = selected
        selected_values[field] = selected
    for field in MERGEABLE_FIELDS:
        canonical[field] = _choose_field_value(rows_by_mode, field)
        selected_values[field] = str(canonical[field] or "").strip()
    return canonical, {
        "identity_conflicts": identity_conflicts,
        "selected_values": selected_values,
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


def _load_role_map(
    connection: sqlite3.Connection,
    *,
    table: str,
    text_field: str,
    target_field: str,
    order_field: str,
    paragraph_ids: list[str],
) -> dict[str, str]:
    placeholders = ",".join("?" for _ in paragraph_ids)
    query = (
        f"SELECT paragraph_id, {text_field}, {target_field} FROM {table} "
        f"WHERE paragraph_id IN ({placeholders}) ORDER BY paragraph_id, {order_field}"
    )
    mapping: dict[str, list[dict[str, str]]] = {paragraph_id: [] for paragraph_id in paragraph_ids}
    for paragraph_id, text, target in connection.execute(query, paragraph_ids):
        mapping[str(paragraph_id)].append(
            {"text": str(text or "").strip(), "target": str(target or "").strip()}
        )
    return {
        paragraph_id: json.dumps(rows, separators=(",", ":"))
        for paragraph_id, rows in mapping.items()
    }


def _load_paragraph_rows(db_path: Path, paragraph_ids: list[str]) -> list[dict[str, Any]]:
    if not paragraph_ids:
        return []
    connection = sqlite3.connect(str(db_path))
    try:
        placeholders = ",".join("?" for _ in paragraph_ids)
        query = (
            "SELECT p.paragraph_id, p.paragraph_link, p.document_link, p.section_link, p.chapter, "
            "p.section, p.paragraph_number, p.text, p.clean_text, p.checksum, c.source_sha256 "
            "FROM paragraphs p LEFT JOIN chunks c ON c.chunk_uid = p.paragraph_id "
            f"WHERE p.paragraph_id IN ({placeholders})"
        )
        rows = connection.execute(query, paragraph_ids).fetchall()
        defined_map = _load_role_map(
            connection,
            table="fls_paragraph_defined_terms",
            text_field="term_text",
            target_field="term_target",
            order_field="term_order",
            paragraph_ids=paragraph_ids,
        )
        term_ref_map = _load_role_map(
            connection,
            table="fls_paragraph_term_refs",
            text_field="term_text",
            target_field="term_target",
            order_field="term_order",
            paragraph_ids=paragraph_ids,
        )
        syntax_def_map = _load_role_map(
            connection,
            table="fls_paragraph_syntax_defs",
            text_field="symbol_text",
            target_field="symbol_target",
            order_field="symbol_order",
            paragraph_ids=paragraph_ids,
        )
        syntax_ref_map = _load_role_map(
            connection,
            table="fls_paragraph_syntax_refs",
            text_field="symbol_text",
            target_field="symbol_target",
            order_field="symbol_order",
            paragraph_ids=paragraph_ids,
        )
        std_ref_map = _load_role_map(
            connection,
            table="fls_paragraph_std_refs",
            text_field="symbol_text",
            target_field="symbol_target",
            order_field="symbol_order",
            paragraph_ids=paragraph_ids,
        )
        out: list[dict[str, Any]] = []
        for (
            paragraph_id,
            paragraph_link,
            document_link,
            section_link,
            chapter,
            section,
            paragraph_number,
            text,
            clean_text,
            checksum,
            source_sha256,
        ) in rows:
            pid = str(paragraph_id)
            out.append(
                {
                    "chunk_uid": pid,
                    "paragraph_id": pid,
                    "paragraph_link": str(paragraph_link or "").strip(),
                    "document_link": str(document_link or "").strip(),
                    "section_link": str(section_link or "").strip(),
                    "section_heading": str(section or "").strip(),
                    "chapter": str(chapter or "").strip(),
                    "section": str(section or "").strip(),
                    "paragraph_number": str(paragraph_number or "").strip(),
                    "text": str(text or ""),
                    "chunk_text": str(clean_text or text or ""),
                    "defined_terms_json": defined_map.get(pid, "[]"),
                    "term_refs_json": term_ref_map.get(pid, "[]"),
                    "syntax_defs_json": syntax_def_map.get(pid, "[]"),
                    "syntax_refs_json": syntax_ref_map.get(pid, "[]"),
                    "std_refs_json": std_ref_map.get(pid, "[]"),
                    "source_anchor": str(paragraph_link or "").strip(),
                    "checksum": str(checksum or source_sha256 or "").strip(),
                }
            )
        return out
    finally:
        connection.close()


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
    supplemental_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    components_cfg = dict((policy.get("scoring") or {}).get("components") or {})
    qualification_cfg = dict(policy.get("qualification") or {})
    glossary_penalty_value = float(
        (policy.get("scoring") or {}).get("glossary_terminal_penalty", 0.0) or 0.0
    )
    component_evidence_min = float(qualification_cfg.get("component_evidence_min", 0.0) or 0.0)
    total_score_min = float(qualification_cfg.get("total_score_min", 0.0) or 0.0)
    candidates_by_id: dict[str, dict[str, Any]] = {}

    for mode in MODE_ORDER:
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

    for row in list(supplemental_rows or []):
        identity = _candidate_identity(row)
        paragraph_id = identity["paragraph_id"]
        if not paragraph_id or paragraph_id in candidates_by_id:
            continue
        first_seen_stage = first_seen_by_paragraph.setdefault(paragraph_id, stage)
        candidates_by_id[paragraph_id] = {
            "paragraph_id": paragraph_id,
            "first_seen_stage": first_seen_stage,
            "retrieval_stage": stage,
            "seen_in_modes": [],
            "mode_row_refs": [],
            "rows_by_mode": {"supplemental": dict(row)},
        }

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
        glossary_candidate = _is_glossary_candidate(canonical_row)
        score_components = {
            "text_overlap_score": _overlap_score(
                str(canonical_row.get("chunk_text", canonical_row.get("text", ""))), packet_tokens
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
                "glossary_candidate": glossary_candidate,
                "matched_role_features": {
                    "defined_terms": defined_matches,
                    "term_refs": term_ref_matches,
                    "syntax": syntax_matches,
                    "std_refs": std_matches,
                },
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
            top_candidate["total_score"] = round(float(top_candidate["total_score"]) + penalty, 6)
            ranked_candidates.sort(
                key=lambda row: (
                    -float(row.get("total_score", 0.0)),
                    -float(row.get("score_components", {}).get("text_overlap_score", 0.0)),
                    str(row.get("paragraph_id", "")),
                )
            )
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

    non_glossary_rows = [
        row
        for row in prior_rows
        if isinstance(row, dict) and not _is_glossary_link(str(row.get(key, "")))
    ]
    if non_glossary_rows:
        prior_rows = non_glossary_rows
    elif stage == "section" and prior_rows:
        return [], {
            "state": "restricted_empty",
            "allowed_paragraph_ids": [],
            "source_links": [],
        }

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


def _top_section_scope_ids(
    *,
    stage_candidates: list[dict[str, Any]],
    topology_index: dict[str, Any],
    limit: int = 3,
) -> list[str]:
    section_links: list[str] = []
    seen: set[str] = set()
    for row in stage_candidates:
        section_link = str(row.get("section_link", "")).strip()
        if not section_link or section_link in seen:
            continue
        if _is_glossary_link(section_link):
            continue
        seen.add(section_link)
        section_links.append(section_link)
        if len(section_links) >= limit:
            break
    paragraph_ids: list[str] = []
    seen_ids: set[str] = set()
    for section_link in section_links:
        for paragraph_id in paragraph_ids_for_section(topology_index, section_link):
            if paragraph_id in seen_ids:
                continue
            seen_ids.add(paragraph_id)
            paragraph_ids.append(paragraph_id)
    return paragraph_ids


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
    query_text = _build_query_text(project_root, packet)
    _trace_event(
        policy,
        {
            "event": "stage_start",
            "stage": stage,
            "scope_state": scope_info["state"],
            "source_links": list(scope_info["source_links"]),
            "allowed_paragraph_count": len(scope_info["allowed_paragraph_ids"]),
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

    def run_mode_queries(*, allowed_statement_ids: list[str] | None) -> dict[str, dict[str, Any]]:
        local_results: dict[str, dict[str, Any]] = {}
        for mode in MODE_ORDER:
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
                rewrite_mode="auto",
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
    if not qualifying_candidates and stage in {"document", "global"}:
        refinement_scope_ids = _top_section_scope_ids(
            stage_candidates=stage_candidates,
            topology_index=topology_index,
        )
        if refinement_scope_ids:
            seen_ids = {
                str(row.get("paragraph_id", "")).strip()
                for row in stage_candidates
                if str(row.get("paragraph_id", "")).strip()
            }
            supplemental_rows = [
                row
                for row in _load_paragraph_rows(db_path, refinement_scope_ids)
                if str(row.get("paragraph_id", "")).strip() not in seen_ids
            ]
            stage_candidates = _merge_stage_candidates(
                stage=stage,
                mode_results=mode_results,
                prior_documents=list(packet.get("prior_documents") or []),
                prior_sections=list(packet.get("prior_sections") or []),
                packet_tokens=packet_tokens,
                topology_index=topology_index,
                policy=policy,
                first_seen_by_paragraph=first_seen_by_paragraph,
                supplemental_rows=supplemental_rows,
            )
            qualifying_candidates = [
                row for row in stage_candidates if bool(row.get("qualifying_candidate", False))
            ]
    qualifying_by_mode = {
        mode: sum(
            1 for row in qualifying_candidates if mode in list(row.get("seen_in_modes") or [])
        )
        for mode in MODE_ORDER
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
                mode: len(list(mode_results[mode].get("rows") or [])) for mode in MODE_ORDER
            },
            "mode_qualifying_counts": qualifying_by_mode,
            "top_candidates": [
                {
                    "paragraph_id": str(row.get("paragraph_id", "")),
                    "total_score": float(row.get("total_score", 0.0) or 0.0),
                    "glossary_candidate": bool(row.get("glossary_candidate", False)),
                }
                for row in stage_candidates[:3]
            ],
        },
    )
    return {
        "stage_name": stage,
        "query_text": query_text,
        "scope": {
            **scope_info,
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
                for mode in MODE_ORDER
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
    candidates = list(stage_result.get("qualifying_candidates") or [])
    if not candidates:
        _trace_event(
            policy,
            {
                "event": "stage_decision",
                "stage": str(stage_result.get("stage_name", "")),
                "reason_code": REASON_NO_QUALIFYING,
                "accepted": False,
                "review_candidate": False,
            },
        )
        return None, {
            "reason_code": REASON_NO_QUALIFYING,
            "publish_accept": False,
            "review_candidate": False,
            "accepted": False,
            "glossary_override_applied": False,
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

    if (
        float(top.get("total_score", 0.0)) >= acceptance_total_min
        and margin >= acceptance_margin_min
    ):
        _trace_event(
            policy,
            {
                "event": "stage_decision",
                "stage": str(stage_result.get("stage_name", "")),
                "reason_code": REASON_ACCEPTED,
                "accepted": True,
                "review_candidate": False,
                "paragraph_id": str(top.get("paragraph_id", "")),
                "total_score": float(top.get("total_score", 0.0) or 0.0),
                "decision_margin": float(top.get("decision_margin", 0.0) or 0.0),
                "glossary_override_applied": glossary_override_applied,
            },
        )
        return top, {
            "reason_code": REASON_ACCEPTED,
            "publish_accept": True,
            "review_candidate": False,
            "accepted": True,
            "glossary_override_applied": glossary_override_applied,
        }

    if float(top.get("total_score", 0.0)) >= review_total_min:
        reason_code = REASON_AMBIGUOUS if margin < acceptance_margin_min else REASON_REVIEW
        _trace_event(
            policy,
            {
                "event": "stage_decision",
                "stage": str(stage_result.get("stage_name", "")),
                "reason_code": reason_code,
                "accepted": False,
                "review_candidate": True,
                "paragraph_id": str(top.get("paragraph_id", "")),
                "total_score": float(top.get("total_score", 0.0) or 0.0),
                "decision_margin": float(top.get("decision_margin", 0.0) or 0.0),
                "glossary_override_applied": glossary_override_applied,
            },
        )
        return top, {
            "reason_code": reason_code,
            "publish_accept": False,
            "review_candidate": True,
            "accepted": False,
            "glossary_override_applied": glossary_override_applied,
        }

    _trace_event(
        policy,
        {
            "event": "stage_decision",
            "stage": str(stage_result.get("stage_name", "")),
            "reason_code": REASON_NO_QUALIFYING,
            "accepted": False,
            "review_candidate": False,
            "glossary_override_applied": glossary_override_applied,
        },
    )
    return None, {
        "reason_code": REASON_NO_QUALIFYING,
        "publish_accept": False,
        "review_candidate": False,
        "accepted": False,
        "glossary_override_applied": glossary_override_applied,
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
            packet=packet,
            topology_index=topology_index,
            db_path=db_path,
            runtime_settings=runtime_settings,
            policy=policy,
            first_seen_by_paragraph=first_seen_by_paragraph,
        )
        stage_results.append(stage_result)
        stage_artifact = dict(stage_result["stage_artifact"])
        candidate, stage_decision = _select_stage_winner(stage_result, policy=policy)
        if validation_only_continuation and candidate is not None and stage != STAGE_ORDER[-1]:
            stage_artifact["advancement_reason"] = ADVANCEMENT_VALIDATION_ONLY
        stage_artifacts.append(stage_artifact)
        if candidate is not None:
            if bool(stage_decision.get("accepted", False)):
                decision.update(stage_decision)
                selected_stage = stage
                selected_candidate = candidate
                if not validation_only_continuation:
                    break
            else:
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
