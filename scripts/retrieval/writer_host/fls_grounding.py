from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any


TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+")
PROSE_FRAGMENT_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_'-]*")
FORMAT_PREFIX_PATTERN = re.compile(r"^(?:[|*\-]+\s+)+")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = PROJECT_ROOT / ".cache" / "sqlite_kb" / "current" / "fls_spec.db"
STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "when",
    "into",
    "shall",
    "must",
    "code",
    "guideline",
    "rust",
    "paths",
    "path",
    "using",
}
MAX_CONSTRUCT_TERMS = 12
MAX_SUPPORTING_PHRASES = 8
MAX_DOCUMENT_PRIORS = 3
MAX_SECTION_PRIORS = 5
GLOSSARY_PRIOR_DAMPING = 0.2


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _claim_texts(draft: dict[str, Any]) -> list[str]:
    claim_map = draft.get("claim_to_evidence_map")
    if not isinstance(claim_map, list):
        return []
    out: list[str] = []
    for row in claim_map:
        if not isinstance(row, dict):
            continue
        claim_text = _text(row.get("claim_text"))
        if claim_text and claim_text not in out:
            out.append(claim_text)
    return out


def _split_sentences(text: str) -> list[str]:
    normalized = _text(text)
    if not normalized:
        return []
    parts = [part.strip() for part in SENTENCE_SPLIT_PATTERN.split(normalized) if part.strip()]
    return parts or [normalized]


def _tokenize_terms(text: str) -> list[str]:
    out: list[str] = []
    for token in TOKEN_PATTERN.findall(text.lower()):
        if len(token) < 3 or token in STOPWORDS:
            continue
        if token in out:
            continue
        out.append(token)
        if len(out) >= MAX_CONSTRUCT_TERMS:
            break
    return out


def _unique_preserve(values: list[str], *, limit: int | None = None) -> list[str]:
    out: list[str] = []
    for value in values:
        text = _text(value)
        if not text or text in out:
            continue
        out.append(text)
        if limit is not None and len(out) >= limit:
            break
    return out


def _code_tokens(examples: dict[str, Any]) -> list[str]:
    raw_lines = (
        f"{_text(examples.get('non_compliant_code'))}\n{_text(examples.get('compliant_code'))}"
    ).splitlines()
    code_lines: list[str] = []
    for raw_line in raw_lines:
        line = raw_line.split("//", 1)[0].rstrip()
        stripped = line.lstrip()
        if stripped.startswith("# "):
            continue
        if stripped == "#":
            continue
        if not stripped:
            continue
        code_lines.append(line)
    text = "\n".join(code_lines)
    out: list[str] = []
    for token in TOKEN_PATTERN.findall(text):
        lowered = token.lower()
        if len(lowered) < 2 or lowered in STOPWORDS or lowered in out:
            continue
        out.append(lowered)
    return out


def _supporting_phrases(
    *,
    title: str,
    claim_texts: list[str],
    amplification_text: str,
    rationale_text: str,
) -> list[str]:
    phrases: list[str] = []
    if title:
        phrases.append(title)
    for claim in claim_texts:
        phrases.extend(_split_sentences(claim))
    phrases.extend(_split_sentences(amplification_text))
    phrases.extend(_split_sentences(rationale_text))
    cleaned: list[str] = []
    for phrase in phrases:
        normalized = _text(phrase)
        normalized = FORMAT_PREFIX_PATTERN.sub("", normalized)
        normalized = normalized.replace("**", "").replace("``", "")
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if len(PROSE_FRAGMENT_PATTERN.findall(normalized)) < 2:
            continue
        if normalized.count("|") > 0 and len(PROSE_FRAGMENT_PATTERN.findall(normalized)) < 4:
            continue
        if normalized.startswith(":"):
            continue
        cleaned.append(normalized)
    return _unique_preserve(cleaned, limit=MAX_SUPPORTING_PHRASES)


def _governing_obligation(*, title: str, claim_texts: list[str], amplification_text: str) -> str:
    amplification_sentences = _split_sentences(amplification_text)
    if amplification_sentences:
        return amplification_sentences[0]
    if claim_texts:
        return claim_texts[0]
    return title


def _construct_terms(*, title: str, draft_terms: Any, claim_texts: list[str]) -> list[str]:
    provided: list[str] = []
    if isinstance(draft_terms, list):
        for value in draft_terms:
            for token in _tokenize_terms(_text(value)):
                if token not in provided:
                    provided.append(token)
                if len(provided) >= MAX_CONSTRUCT_TERMS:
                    break
            if len(provided) >= MAX_CONSTRUCT_TERMS:
                break
    if provided:
        return provided
    derived = _tokenize_terms(title)
    for claim in claim_texts:
        for token in _tokenize_terms(claim):
            if token not in derived:
                derived.append(token)
            if len(derived) >= MAX_CONSTRUCT_TERMS:
                return derived
    return derived


def _evidence_tokens(grounding: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for term in list(grounding.get("construct_terms") or []):
        text = _text(term).lower()
        if text and text not in tokens:
            tokens.append(text)
    for term in list(grounding.get("code_tokens") or []):
        text = _text(term).lower()
        if text and text not in tokens:
            tokens.append(text)
    for phrase in list(grounding.get("supporting_phrases") or []):
        for token in _tokenize_terms(phrase):
            if token not in tokens:
                tokens.append(token)
    return tokens


def _match_count(text: str, evidence_tokens: list[str]) -> int:
    haystack = {token.lower() for token in TOKEN_PATTERN.findall(text)}
    return sum(1 for token in evidence_tokens if token in haystack)


ROLE_FEATURE_TABLES: tuple[tuple[str, str, str, str], ...] = (
    ("fls_paragraph_defined_terms", "term_text", "term_target", "defined_term"),
    ("fls_paragraph_term_refs", "term_text", "term_target", "term_ref"),
    ("fls_paragraph_syntax_defs", "symbol_text", "symbol_target", "syntax_def"),
    ("fls_paragraph_syntax_refs", "symbol_text", "symbol_target", "syntax_ref"),
    ("fls_paragraph_std_refs", "symbol_text", "symbol_target", "std_ref"),
)


def _role_feature_counts_by_document(
    connection: sqlite3.Connection, evidence_tokens: list[str]
) -> dict[str, dict[str, set[str]]]:
    counts: dict[str, dict[str, set[str]]] = {}
    if not evidence_tokens:
        return counts
    for table_name, text_column, target_column, feature_name in ROLE_FEATURE_TABLES:
        rows = connection.execute(
            f"""
            SELECT p.document_link, lower(t.{text_column}) AS text_value, lower(t.{target_column}) AS target_value
            FROM {table_name} AS t
            JOIN paragraphs AS p ON p.paragraph_id = t.paragraph_id
            WHERE p.retrieval_eligible = 1
            """
        ).fetchall()
        for document_link, text_value, target_value in rows:
            row_tokens = {token for token in TOKEN_PATTERN.findall(_text(text_value))}
            row_tokens.update(token for token in TOKEN_PATTERN.findall(_text(target_value)))
            matched_tokens = {token for token in evidence_tokens if token in row_tokens}
            if matched_tokens:
                bucket = counts.setdefault(str(document_link), {})
                feature_bucket = bucket.setdefault(feature_name, set())
                feature_bucket.update(matched_tokens)
    return counts


def _role_feature_counts_by_section(
    connection: sqlite3.Connection, evidence_tokens: list[str]
) -> dict[str, dict[str, set[str]]]:
    counts: dict[str, dict[str, set[str]]] = {}
    if not evidence_tokens:
        return counts
    for table_name, text_column, target_column, feature_name in ROLE_FEATURE_TABLES:
        rows = connection.execute(
            f"""
            SELECT p.section_link, lower(t.{text_column}) AS text_value, lower(t.{target_column}) AS target_value
            FROM {table_name} AS t
            JOIN paragraphs AS p ON p.paragraph_id = t.paragraph_id
            WHERE p.retrieval_eligible = 1
            """
        ).fetchall()
        for section_link, text_value, target_value in rows:
            row_tokens = {token for token in TOKEN_PATTERN.findall(_text(text_value))}
            row_tokens.update(token for token in TOKEN_PATTERN.findall(_text(target_value)))
            matched_tokens = {token for token in evidence_tokens if token in row_tokens}
            if matched_tokens:
                bucket = counts.setdefault(str(section_link), {})
                feature_bucket = bucket.setdefault(feature_name, set())
                feature_bucket.update(matched_tokens)
    return counts


def _role_feature_labels(role_counts: dict[str, set[str]]) -> list[str]:
    labels: list[str] = []
    for feature_name in sorted(role_counts):
        count = len(role_counts.get(feature_name, set()) or set())
        if count <= 0:
            continue
        labels.append(f"{feature_name}:{count}")
    return labels


def _normalize_priors(
    rows: list[dict[str, Any]], *, link_key: str, limit: int
) -> list[dict[str, Any]]:
    if not rows:
        return []
    max_raw = max(float(row["raw_score"]) for row in rows)
    scale = max(1.0, max_raw)
    normalized: list[dict[str, Any]] = []
    for row in rows:
        evidence_raw = row.get("evidence")
        if isinstance(evidence_raw, dict):
            evidence = dict(evidence_raw)
        else:
            evidence = {}
        normalized.append(
            {
                link_key: str(row["link"]),
                "score": round(float(row["raw_score"]) / scale, 6),
                "evidence": {
                    "document_title_hits": _unique_preserve(
                        list(evidence.get("document_title_hits") or [])
                    ),
                    "section_title_hits": _unique_preserve(
                        list(evidence.get("section_title_hits") or [])
                    ),
                    "role_feature_hits": _unique_preserve(
                        list(evidence.get("role_feature_hits") or [])
                    ),
                },
                "_raw_score": float(row["raw_score"]),
                "_title_hits": int(row.get("title_hits", 0) or 0),
                "_section_hits": int(row.get("section_hits", 0) or 0),
                "_role_hits": int(row.get("role_hits", 0) or 0),
                "_role_feature_types": int(row.get("role_feature_types", 0) or 0),
            }
        )
    normalized.sort(
        key=lambda row: (
            -float(row["_raw_score"]),
            -int(row["_title_hits"]),
            -int(row["_section_hits"]),
            -int(row["_role_feature_types"]),
            -int(row["_role_hits"]),
            str(row[link_key]),
        )
    )
    trimmed = normalized[:limit]
    for row in trimmed:
        for key in (
            "_raw_score",
            "_title_hits",
            "_section_hits",
            "_role_hits",
            "_role_feature_types",
        ):
            row.pop(key, None)
    return trimmed


def _apply_glossary_prior_damping(link: str, raw_score: float) -> float:
    normalized_link = _text(link).lower()
    if not normalized_link.startswith("glossary.html"):
        return raw_score
    return raw_score * GLOSSARY_PRIOR_DAMPING


def _prior_documents(
    connection: sqlite3.Connection, grounding: dict[str, Any]
) -> list[dict[str, Any]]:
    evidence_tokens = _evidence_tokens(grounding)
    role_counts = _role_feature_counts_by_document(connection, evidence_tokens)
    section_rows = connection.execute(
        "SELECT document_link, title FROM fls_sections ORDER BY ordinal ASC"
    ).fetchall()
    section_hits_by_document: dict[str, list[str]] = {}
    for document_link, section_title in section_rows:
        hits = [token for token in evidence_tokens if token in _tokenize_terms(str(section_title))]
        if not hits:
            continue
        bucket = section_hits_by_document.setdefault(str(document_link), [])
        for token in hits:
            if token not in bucket:
                bucket.append(token)
    rows = connection.execute(
        "SELECT document_link, title FROM fls_documents ORDER BY ordinal ASC"
    ).fetchall()
    scored: list[dict[str, Any]] = []
    for document_link, title in rows:
        title_token_hits = [
            token for token in evidence_tokens if token in _tokenize_terms(str(title))
        ]
        title_hits = len(title_token_hits)
        section_title_hits = list(section_hits_by_document.get(str(document_link), []))
        section_hits = len(section_title_hits)
        document_role_counts = {
            feature_name: set(values)
            for feature_name, values in dict(role_counts.get(str(document_link), {})).items()
        }
        role_hits = sum(len(values) for values in document_role_counts.values())
        role_feature_types = sum(1 for value in document_role_counts.values() if len(value) > 0)
        raw_score = (
            3.0 * title_hits + 1.5 * section_hits + 0.2 * role_hits + 0.35 * role_feature_types
        )
        raw_score = _apply_glossary_prior_damping(str(document_link), raw_score)
        evidence = {
            "document_title_hits": title_token_hits,
            "section_title_hits": section_title_hits,
            "role_feature_hits": _role_feature_labels(document_role_counts),
        }
        if raw_score <= 0.0:
            continue
        scored.append(
            {
                "link": str(document_link),
                "raw_score": raw_score,
                "evidence": evidence,
                "title_hits": title_hits,
                "section_hits": section_hits,
                "role_hits": role_hits,
                "role_feature_types": role_feature_types,
            }
        )
    return _normalize_priors(scored, link_key="document_link", limit=MAX_DOCUMENT_PRIORS)


def _prior_sections(
    connection: sqlite3.Connection, grounding: dict[str, Any]
) -> list[dict[str, Any]]:
    evidence_tokens = _evidence_tokens(grounding)
    role_counts = _role_feature_counts_by_section(connection, evidence_tokens)
    rows = connection.execute(
        "SELECT section_link, title FROM fls_sections ORDER BY ordinal ASC"
    ).fetchall()
    scored: list[dict[str, Any]] = []
    for section_link, title in rows:
        title_token_hits = [
            token for token in evidence_tokens if token in _tokenize_terms(str(title))
        ]
        title_hits = len(title_token_hits)
        section_role_counts = {
            feature_name: set(values)
            for feature_name, values in dict(role_counts.get(str(section_link), {})).items()
        }
        role_hits = sum(len(values) for values in section_role_counts.values())
        role_feature_types = sum(1 for value in section_role_counts.values() if len(value) > 0)
        raw_score = 3.5 * title_hits + 0.25 * role_hits + 0.35 * role_feature_types
        raw_score = _apply_glossary_prior_damping(str(section_link), raw_score)
        evidence = {
            "document_title_hits": [],
            "section_title_hits": title_token_hits,
            "role_feature_hits": _role_feature_labels(section_role_counts),
        }
        if raw_score <= 0.0:
            continue
        scored.append(
            {
                "link": str(section_link),
                "raw_score": raw_score,
                "evidence": evidence,
                "title_hits": 0,
                "section_hits": title_hits,
                "role_hits": role_hits,
                "role_feature_types": role_feature_types,
            }
        )
    return _normalize_priors(scored, link_key="section_link", limit=MAX_SECTION_PRIORS)


def build_grounding_artifact(row: dict[str, Any], *, db_path: Path | None = None) -> dict[str, Any]:
    draft = _as_dict(row.get("draft"))
    amplification = _as_dict(row.get("amplification"))
    rationale = _as_dict(row.get("rationale"))
    examples = _as_dict(row.get("examples"))

    title = _text(draft.get("title"))
    claim_texts = _claim_texts(draft)
    amplification_text = _text(amplification.get("guideline_amplification_text"))
    rationale_text = _text(rationale.get("rationale_text"))

    construct_terms = _construct_terms(
        title=title,
        draft_terms=draft.get("construct_terms"),
        claim_texts=claim_texts,
    )
    supporting_phrases = _supporting_phrases(
        title=title,
        claim_texts=claim_texts,
        amplification_text=amplification_text,
        rationale_text=rationale_text,
    )
    artifact = {
        "governing_obligation": _governing_obligation(
            title=title,
            claim_texts=claim_texts,
            amplification_text=amplification_text,
        ),
        "construct_terms": construct_terms,
        "code_tokens": _code_tokens(examples),
        "supporting_phrases": supporting_phrases,
        "prior_documents": [],
        "prior_sections": [],
        "ambiguity_notes": [],
    }

    ambiguity_notes: list[str] = []
    if not title:
        ambiguity_notes.append("missing_draft_title")
    if not construct_terms:
        ambiguity_notes.append("missing_construct_terms")
    if not artifact["code_tokens"]:
        ambiguity_notes.append("missing_code_tokens")

    resolved_db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if resolved_db_path.exists():
        with sqlite3.connect(resolved_db_path) as connection:
            artifact["prior_documents"] = _prior_documents(connection, artifact)
            artifact["prior_sections"] = _prior_sections(connection, artifact)
    if not artifact["prior_documents"]:
        ambiguity_notes.append("broad_document_priors")
    if not artifact["prior_sections"]:
        ambiguity_notes.append("broad_section_priors")
    artifact["ambiguity_notes"] = _unique_preserve(ambiguity_notes)
    return artifact
