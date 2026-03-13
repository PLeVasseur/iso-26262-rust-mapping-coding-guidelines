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
    "also",
    "same",
}
GENERIC_TOKENS = {
    "type",
    "pointer",
    "unsafe",
    "expression",
    "function",
    "struct",
    "block",
    "attribute",
}
HUB_CONTENT_TYPES = {"glossary", "inventory", "index"}
MAX_CONSTRUCT_TERMS = 12
MAX_SUPPORTING_PHRASES = 8
MAX_DOCUMENT_PRIORS = 3
MAX_SECTION_PRIORS = 5
MAX_PHRASE_DERIVED_TOKENS = 8


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


def _phrase_tokens(text: str) -> list[str]:
    def _normalize_piece(piece: str) -> str:
        if len(piece) > 4 and piece.endswith("s") and not piece.endswith("ss"):
            return piece[:-1]
        return piece

    tokens: list[str] = []
    for token in PROSE_FRAGMENT_PATTERN.findall(text.lower()):
        normalized = token.replace("_", " ").replace("-", " ").replace("'", " ")
        for piece in normalized.split():
            normalized_piece = _normalize_piece(piece)
            if len(normalized_piece) < 2:
                continue
            tokens.append(normalized_piece)
    return tokens


def _normalize_phrase(text: str) -> str:
    return " ".join(_phrase_tokens(text))


def _normalized_non_stopword_tokens(text: str) -> list[str]:
    return [token for token in _phrase_tokens(text) if token not in STOPWORDS]


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


def _unique_preserve(values: list[Any], *, limit: int | None = None) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str):
            normalized: Any = _text(value)
            marker = normalized
        else:
            normalized = value
            marker = repr(value)
        if not marker or marker in seen:
            continue
        seen.add(marker)
        out.append(normalized)
        if limit is not None and len(out) >= limit:
            break
    return out


def _generic_token_ratio(tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    hits = sum(1 for token in tokens if token in GENERIC_TOKENS)
    return round(hits / max(1, len(tokens)), 6)


def _phrase_specificity_score(tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    generic_ratio = _generic_token_ratio(tokens)
    non_generic = [token for token in tokens if token not in GENERIC_TOKENS]
    length_score = min(1.0, len(tokens) / 6.0)
    distinctiveness_score = min(1.0, len({token for token in non_generic if len(token) >= 5}) / 3.0)
    score = 0.45 * (1.0 - generic_ratio) + 0.35 * length_score + 0.20 * distinctiveness_score
    return round(max(0.0, min(1.0, score)), 6)


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


def _phrase_records(grounding: dict[str, Any]) -> list[dict[str, Any]]:
    phrases = _unique_preserve(
        [_text(grounding.get("governing_obligation"))]
        + list(grounding.get("supporting_phrases") or []),
        limit=MAX_SUPPORTING_PHRASES + 1,
    )
    records: list[dict[str, Any]] = []
    for phrase in phrases:
        tokens = _phrase_tokens(str(phrase))
        non_stopword_tokens = [token for token in tokens if token not in STOPWORDS]
        record = {
            "text": str(phrase),
            "normalized_text": _normalize_phrase(str(phrase)),
            "tokens": tokens,
            "token_count": len(tokens),
            "generic_token_ratio": _generic_token_ratio(non_stopword_tokens),
            "specificity_score": _phrase_specificity_score(non_stopword_tokens),
        }
        if record["normalized_text"]:
            records.append(record)
    return records


def _token_records(grounding: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for term in list(grounding.get("construct_terms") or []):
        for token in _normalized_non_stopword_tokens(_text(term)):
            if token in seen:
                continue
            seen.add(token)
            records.append(
                {"token": token, "source": "construct_term", "generic": token in GENERIC_TOKENS}
            )
    phrase_budget = MAX_PHRASE_DERIVED_TOKENS
    for phrase in _phrase_records(grounding):
        if phrase_budget <= 0:
            break
        if int(phrase.get("token_count", 0) or 0) < 3:
            continue
        if float(phrase.get("specificity_score", 0.0) or 0.0) < 0.6:
            continue
        for token in list(phrase.get("tokens") or []):
            if token in STOPWORDS or token in GENERIC_TOKENS or token in seen:
                continue
            seen.add(token)
            records.append({"token": token, "source": "phrase", "generic": False})
            phrase_budget -= 1
            if phrase_budget <= 0:
                break
    return records


def _code_records(grounding: dict[str, Any]) -> list[dict[str, Any]]:
    syntax_tokens = {
        "as",
        "const",
        "enum",
        "extern",
        "fn",
        "impl",
        "let",
        "mut",
        "struct",
        "trait",
        "unsafe",
        "use",
    }
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for token in list(grounding.get("code_tokens") or []):
        normalized = _text(token).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        kind = "syntax_token" if normalized in syntax_tokens else "api_symbol"
        records.append({"token": normalized, "kind": kind})
    return records


def _structural_signals(
    phrase_records: list[dict[str, Any]],
    token_records: list[dict[str, Any]],
    code_records: list[dict[str, Any]],
) -> dict[str, Any]:
    token_pool = {str(record.get("token", "")) for record in token_records + code_records}
    for phrase in phrase_records:
        token_pool.update(str(token) for token in list(phrase.get("tokens") or []))
    return {
        "has_phrase_records": bool(phrase_records),
        "has_code_records": bool(code_records),
        "mentions_attribute": "attribute" in token_pool,
        "mentions_cast": "cast" in token_pool or "casts" in token_pool,
        "mentions_extern": "extern" in token_pool,
        "mentions_pointer": "pointer" in token_pool,
        "mentions_type": "type" in token_pool or "types" in token_pool,
        "mentions_unsafe": "unsafe" in token_pool,
    }


ROLE_FEATURE_TABLES: tuple[tuple[str, str, str, str], ...] = (
    ("fls_paragraph_defined_terms", "term_text", "term_target", "defined_term"),
    ("fls_paragraph_term_refs", "term_text", "term_target", "term_ref"),
    ("fls_paragraph_syntax_defs", "symbol_text", "symbol_target", "syntax_def"),
    ("fls_paragraph_syntax_refs", "symbol_text", "symbol_target", "syntax_ref"),
    ("fls_paragraph_std_refs", "symbol_text", "symbol_target", "std_ref"),
)


def _role_feature_counts(
    connection: sqlite3.Connection, *, scope_column: str, evidence_tokens: list[str]
) -> dict[str, dict[str, set[str]]]:
    counts: dict[str, dict[str, set[str]]] = {}
    if not evidence_tokens:
        return counts
    for table_name, text_column, target_column, feature_name in ROLE_FEATURE_TABLES:
        rows = connection.execute(
            f"""
            SELECT p.{scope_column}, lower(t.{text_column}) AS text_value, lower(t.{target_column}) AS target_value
            FROM {table_name} AS t
            JOIN paragraphs AS p ON p.paragraph_id = t.paragraph_id
            WHERE p.retrieval_eligible = 1
            """
        ).fetchall()
        for scope_value, text_value, target_value in rows:
            row_tokens = set(_phrase_tokens(_text(text_value)))
            row_tokens.update(_phrase_tokens(_text(target_value)))
            matched_tokens = {token for token in evidence_tokens if token in row_tokens}
            if not matched_tokens:
                continue
            bucket = counts.setdefault(str(scope_value), {})
            normalized_feature = "syntax_ref" if feature_name == "syntax_def" else feature_name
            feature_bucket = bucket.setdefault(normalized_feature, set())
            feature_bucket.update(matched_tokens)
    return counts


def _role_feature_counts_by_document(
    connection: sqlite3.Connection, evidence_tokens: list[str]
) -> dict[str, dict[str, set[str]]]:
    return _role_feature_counts(
        connection, scope_column="document_link", evidence_tokens=evidence_tokens
    )


def _role_feature_counts_by_section(
    connection: sqlite3.Connection, evidence_tokens: list[str]
) -> dict[str, dict[str, set[str]]]:
    return _role_feature_counts(
        connection, scope_column="section_link", evidence_tokens=evidence_tokens
    )


def _role_feature_labels(role_counts: dict[str, set[str]]) -> list[str]:
    labels: list[str] = []
    for feature_name in sorted(role_counts):
        count = len(role_counts.get(feature_name, set()) or set())
        if count <= 0:
            continue
        labels.append(f"{feature_name}:{count}")
    return labels


def _heading_tokens(text: str) -> list[str]:
    return _phrase_tokens(text)


def _classify_content_type(link: str, title: str) -> str:
    normalized_link = _text(link).lower()
    document_link = normalized_link.split("#", 1)[0]
    normalized_title = _normalize_phrase(title)
    if normalized_link.startswith("glossary.html"):
        return "glossary"
    if "glossary" in normalized_title:
        return "glossary"
    if "index" in normalized_title:
        return "index"
    if any(
        marker in normalized_title
        for marker in (
            "attribute",
            "attributes",
            "built in attribute",
            "built in attributes",
            "keyword",
            "keywords",
            "token",
            "tokens",
        )
    ):
        return "inventory"
    if any(marker in normalized_title for marker in ("example", "examples")):
        return "examples"
    if document_link.endswith(".html") and not document_link.startswith("glossary.html"):
        return "normative"
    return "unknown"


def _document_candidate_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT document_link, title FROM fls_documents ORDER BY ordinal ASC"
    ).fetchall()
    return [
        {
            "scope_kind": "document",
            "link": str(document_link),
            "document_link": str(document_link),
            "section_link": "",
            "title": str(title),
            "heading_tokens": _heading_tokens(str(title)),
            "content_type": _classify_content_type(str(document_link), str(title)),
        }
        for document_link, title in rows
    ]


def _section_candidate_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT section_link, document_link, title FROM fls_sections ORDER BY ordinal ASC"
    ).fetchall()
    return [
        {
            "scope_kind": "section",
            "link": str(section_link),
            "document_link": str(document_link),
            "section_link": str(section_link),
            "title": str(title),
            "heading_tokens": _heading_tokens(str(title)),
            "content_type": _classify_content_type(str(section_link), str(title)),
        }
        for section_link, document_link, title in rows
    ]


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


def _exact_ngram_coverage(needle_tokens: list[str], haystack_tokens: list[str], n: int) -> float:
    if len(needle_tokens) < n or len(haystack_tokens) < n:
        return 0.0
    needle = {
        tuple(needle_tokens[index : index + n]) for index in range(len(needle_tokens) - n + 1)
    }
    haystack = {
        tuple(haystack_tokens[index : index + n]) for index in range(len(haystack_tokens) - n + 1)
    }
    if not needle:
        return 0.0
    return round(len(needle & haystack) / max(1, len(needle)), 6)


def _phrase_match_features(
    candidate_row: dict[str, Any], phrase_records: list[dict[str, Any]]
) -> dict[str, Any]:
    normalized_title = _normalize_phrase(str(candidate_row.get("title", "")))
    heading_tokens = list(candidate_row.get("heading_tokens") or [])
    best_score = 0.0
    best_hit: dict[str, Any] = {}
    phrase_hits: list[dict[str, Any]] = []
    for phrase in phrase_records:
        phrase_tokens = [
            token
            for token in list(phrase.get("tokens") or [])
            if token not in STOPWORDS and token not in GENERIC_TOKENS
        ]
        if not phrase_tokens:
            continue
        exact_phrase_match = (
            1.0 if str(phrase.get("normalized_text", "")) in normalized_title else 0.0
        )
        ordered_non_generic_coverage = _ordered_subsequence_coverage(phrase_tokens, heading_tokens)
        exact_bigram_coverage = _exact_ngram_coverage(phrase_tokens, heading_tokens, 2)
        local_score = max(
            exact_phrase_match,
            0.7 * ordered_non_generic_coverage + 0.3 * exact_bigram_coverage,
        ) * float(phrase.get("specificity_score", 0.0) or 0.0)
        if local_score >= 0.15:
            phrase_hits.append(
                {
                    "text": str(phrase.get("text", "")),
                    "ordered_non_generic_coverage": round(ordered_non_generic_coverage, 6),
                    "exact_bigram_coverage": round(exact_bigram_coverage, 6),
                    "local_score": round(local_score, 6),
                }
            )
        if local_score > best_score:
            best_score = local_score
            best_hit = {
                "best_phrase_text": str(phrase.get("text", "")),
                "best_phrase_exact_match": round(exact_phrase_match, 6),
                "best_phrase_ordered_overlap": round(ordered_non_generic_coverage, 6),
                "best_phrase_bigram_score": round(exact_bigram_coverage, 6),
            }
    return {
        "phrase_specificity_score": round(best_score, 6),
        "phrase_hits": sorted(
            phrase_hits, key=lambda item: (-float(item["local_score"]), item["text"])
        ),
        **best_hit,
    }


def _heading_match_features(
    candidate_row: dict[str, Any],
    token_records: list[dict[str, Any]],
    phrase_records: list[dict[str, Any]],
    structural_signals: dict[str, Any],
) -> dict[str, Any]:
    heading_tokens = set(candidate_row.get("heading_tokens") or [])
    distinctive_tokens = {
        str(record.get("token", ""))
        for record in token_records
        if not bool(record.get("generic", False))
    }
    construct_tokens = {
        str(record.get("token", ""))
        for record in token_records
        if str(record.get("source", "")) == "construct_term"
    }
    generic_construct_tokens = {token for token in construct_tokens if token in GENERIC_TOKENS}
    distinctive_construct_tokens = construct_tokens - generic_construct_tokens
    if not distinctive_tokens:
        for phrase in phrase_records:
            if float(phrase.get("specificity_score", 0.0) or 0.0) < 0.6:
                continue
            distinctive_tokens.update(
                token
                for token in list(phrase.get("tokens") or [])
                if token not in STOPWORDS and token not in GENERIC_TOKENS
            )
    matched_distinctive_tokens = distinctive_tokens & heading_tokens
    matched_construct_terms = distinctive_construct_tokens & heading_tokens
    matched_generic_construct_terms = generic_construct_tokens & heading_tokens
    distinctive_heading_hit_ratio = len(matched_distinctive_tokens) / max(
        1, len(distinctive_tokens)
    )
    exact_construct_term_hit_ratio = len(matched_construct_terms) / max(
        1, len(distinctive_construct_tokens)
    )
    generic_construct_term_hit_ratio = 0.0
    if len(matched_generic_construct_terms) >= 2:
        generic_construct_term_hit_ratio = len(matched_generic_construct_terms) / max(
            1, len(generic_construct_tokens)
        )
    heading_specificity_score = min(
        1.0,
        0.7 * distinctive_heading_hit_ratio
        + 0.15 * exact_construct_term_hit_ratio
        + 0.15 * generic_construct_term_hit_ratio,
    )
    triggered_signals = [
        signal_name
        for signal_name, value in sorted(structural_signals.items())
        if signal_name.startswith("mentions_")
        and bool(value)
        and signal_name.split("mentions_", 1)[1] in heading_tokens
    ]
    return {
        "heading_specificity_score": round(heading_specificity_score, 6),
        "heading_hits": sorted(
            matched_distinctive_tokens | matched_construct_terms | matched_generic_construct_terms
        ),
        "structural_heading_signals": triggered_signals,
    }


def _code_match_features(
    candidate_row: dict[str, Any],
    code_records: list[dict[str, Any]],
    role_counts: dict[str, set[str]],
) -> dict[str, Any]:
    candidate_tokens = set(candidate_row.get("heading_tokens") or [])
    matched = {
        str(record.get("token", ""))
        for record in code_records
        if str(record.get("kind", "")) == "api_symbol"
        if str(record.get("token", "")) in candidate_tokens
    }
    code_token_score = min(1.0, len(matched) / max(1, min(3, len(code_records))))
    return {"code_token_score": round(code_token_score, 6), "code_hits": sorted(matched)}


def _role_match_features(
    role_counts: dict[str, set[str]], content_type: str, phrase_score: float, heading_score: float
) -> dict[str, Any]:
    role_category_hits = {
        "defined_term": bool(role_counts.get("defined_term")),
        "term_ref": bool(role_counts.get("term_ref")),
        "syntax_ref": bool(role_counts.get("syntax_ref")),
        "std_ref": bool(role_counts.get("std_ref")),
    }
    base_role_score = sum(role_category_hits.values()) / 4.0
    if phrase_score < 0.2 and heading_score < 0.2:
        base_role_score *= 0.5
    if content_type == "glossary":
        base_role_score *= 0.6
    elif content_type in {"inventory", "index"}:
        base_role_score *= 0.5
    return {
        "role_feature_score": round(base_role_score, 6),
        "role_feature_hits": _role_feature_labels(role_counts),
        "role_category_hits": role_category_hits,
    }


def _normative_structure_score(content_type: str) -> float:
    if content_type == "normative":
        return 1.0
    if content_type in {"examples", "unknown"}:
        return 0.4
    return 0.0


def _hub_penalty(content_type: str) -> float:
    if content_type == "glossary":
        return 1.0
    if content_type in {"inventory", "index"}:
        return 0.75
    return 0.0


def _diversity_bucket(candidate_row: dict[str, Any]) -> str:
    content_type = str(candidate_row.get("content_type", "unknown"))
    document_link = str(candidate_row.get("document_link", ""))
    if content_type == "normative":
        return f"normative:{document_link or 'document'}"
    if content_type in HUB_CONTENT_TYPES:
        return f"hub:{content_type}"
    return f"other:{content_type}"


def _compute_prior_feature_row(
    candidate_row: dict[str, Any],
    *,
    phrase_records: list[dict[str, Any]],
    token_records: list[dict[str, Any]],
    code_records: list[dict[str, Any]],
    structural_signals: dict[str, Any],
    role_counts: dict[str, set[str]],
) -> dict[str, Any]:
    phrase_features = _phrase_match_features(candidate_row, phrase_records)
    heading_features = _heading_match_features(
        candidate_row, token_records, phrase_records, structural_signals
    )
    code_features = _code_match_features(candidate_row, code_records, role_counts)
    role_features = _role_match_features(
        role_counts,
        str(candidate_row.get("content_type", "unknown")),
        float(phrase_features["phrase_specificity_score"]),
        float(heading_features["heading_specificity_score"]),
    )
    content_type = str(candidate_row.get("content_type", "unknown"))
    normative_structure_score = _normative_structure_score(content_type)
    hub_penalty = _hub_penalty(content_type)
    support_signal = max(
        float(phrase_features["phrase_specificity_score"]),
        float(heading_features["heading_specificity_score"]),
        float(code_features["code_token_score"]),
        float(role_features["role_feature_score"]),
    )
    prior_score = (
        0.30 * float(phrase_features["phrase_specificity_score"])
        + 0.20 * float(heading_features["heading_specificity_score"])
        + 0.15 * float(code_features["code_token_score"])
        + 0.15 * float(role_features["role_feature_score"])
        + 0.20 * normative_structure_score
        - 0.25 * hub_penalty
    )
    if support_signal <= 0.0:
        prior_score = 0.0
    prior_score = round(max(0.0, min(1.0, prior_score)), 6)
    evidence = {
        "phrase_hits": list(phrase_features.get("phrase_hits") or []),
        "heading_hits": list(heading_features.get("heading_hits") or []),
        "role_feature_hits": list(role_features.get("role_feature_hits") or []),
        "code_hits": list(code_features.get("code_hits") or []),
        "normative_signals": [
            signal
            for signal, enabled in {
                "normative_structure": normative_structure_score > 0.0,
                "hub_content": content_type in HUB_CONTENT_TYPES,
            }.items()
            if enabled
        ],
        "hub_penalty_applied": hub_penalty > 0.0,
        "specificity_score": round(
            max(
                float(phrase_features["phrase_specificity_score"]),
                0.6 * float(phrase_features["phrase_specificity_score"])
                + 0.4 * float(heading_features["heading_specificity_score"]),
            ),
            6,
        ),
        "diversity_bucket": _diversity_bucket(candidate_row),
        "feature_breakdown": {
            "phrase_specificity_score": float(phrase_features["phrase_specificity_score"]),
            "heading_specificity_score": float(heading_features["heading_specificity_score"]),
            "code_token_score": float(code_features["code_token_score"]),
            "role_feature_score": float(role_features["role_feature_score"]),
            "normative_structure_score": round(normative_structure_score, 6),
            "hub_penalty": round(hub_penalty, 6),
        },
    }
    return {
        **candidate_row,
        "score": prior_score,
        "evidence": evidence,
    }


def _compute_packet_specificity_state(
    scored_document_rows: list[dict[str, Any]],
    scored_section_rows: list[dict[str, Any]],
    phrase_records: list[dict[str, Any]],
    token_records: list[dict[str, Any]],
) -> dict[str, Any]:
    scored_rows = list(scored_section_rows or scored_document_rows)
    top_rows = scored_rows[:5]
    token_list = [
        str(record.get("token", "")) for record in token_records if str(record.get("token", ""))
    ]
    generic_token_ratio = _generic_token_ratio(token_list)
    distinctive_phrase_count = sum(
        1
        for phrase in phrase_records
        if int(phrase.get("token_count", 0) or 0) >= 3
        and float(phrase.get("generic_token_ratio", 0.0) or 0.0) < 0.6
        and float(phrase.get("specificity_score", 0.0) or 0.0) >= 0.6
    )
    phrase_coverage_ratio = round(distinctive_phrase_count / max(1, len(phrase_records)), 6)
    top_prior_margin = 0.0
    if len(top_rows) >= 2:
        top_prior_margin = round(
            float(top_rows[0].get("score", 0.0) or 0.0)
            - float(top_rows[1].get("score", 0.0) or 0.0),
            6,
        )
    glossary_share = round(
        sum(1 for row in top_rows if str(row.get("content_type", "")) == "glossary")
        / max(1, len(top_rows)),
        6,
    )
    hub_share = round(
        sum(1 for row in top_rows if str(row.get("content_type", "")) in HUB_CONTENT_TYPES)
        / max(1, len(top_rows)),
        6,
    )
    normative_share = round(
        sum(1 for row in top_rows if str(row.get("content_type", "")) == "normative")
        / max(1, len(top_rows)),
        6,
    )
    specificity_reasons: list[str] = []
    if generic_token_ratio >= 0.65:
        specificity_reasons.append("high_generic_token_ratio")
    if distinctive_phrase_count == 0:
        specificity_reasons.append("no_distinctive_phrases")
    if phrase_coverage_ratio < 0.35:
        specificity_reasons.append("low_phrase_coverage_ratio")
    if top_prior_margin < 0.08:
        specificity_reasons.append("low_top_prior_margin")
    if glossary_share >= 0.75 and normative_share == 0:
        specificity_state = "glossary_dominated"
        specificity_reasons.append("glossary_dominated_prior_surface")
    elif generic_token_ratio >= 0.65 and distinctive_phrase_count == 0:
        specificity_state = "low_specificity"
    elif top_prior_margin < 0.08:
        specificity_state = "mixed_specificity"
    else:
        specificity_state = "high_specificity"
    if hub_share >= 0.6:
        specificity_reasons.append("hub_heavy_prior_surface")
    return {
        "specificity_state": specificity_state,
        "specificity_reasons": _unique_preserve(specificity_reasons),
        "generic_token_ratio": generic_token_ratio,
        "distinctive_phrase_count": distinctive_phrase_count,
        "phrase_coverage_ratio": phrase_coverage_ratio,
        "top_prior_margin": top_prior_margin,
        "glossary_share": glossary_share,
        "hub_share": hub_share,
        "normative_share": normative_share,
    }


def _row_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    feature_breakdown = _as_dict(_as_dict(row.get("evidence")).get("feature_breakdown"))
    return (
        -float(row.get("score", 0.0) or 0.0),
        -float(feature_breakdown.get("phrase_specificity_score", 0.0) or 0.0),
        -float(feature_breakdown.get("heading_specificity_score", 0.0) or 0.0),
        str(row.get("link", "")),
    )


def _content_type_pass(content_type: str) -> int:
    if content_type == "normative":
        return 0
    if content_type == "glossary":
        return 2
    return 1


def _select_diversified_priors(
    scored_rows: list[dict[str, Any]], limit: int, scope_kind: str, specificity_state: str
) -> list[dict[str, Any]]:
    if not scored_rows:
        return []
    rows = sorted(
        scored_rows,
        key=lambda row: (_content_type_pass(str(row.get("content_type", "unknown"))),)
        + _row_sort_key(row),
    )
    top_score = max(float(row.get("score", 0.0) or 0.0) for row in scored_rows)
    competitive_non_hub_exists = any(
        str(row.get("content_type", "unknown")) not in HUB_CONTENT_TYPES
        and float(row.get("score", 0.0) or 0.0) >= 0.5 * top_score
        for row in scored_rows
    )
    needs_normative = any(
        str(row.get("content_type", "unknown")) == "normative"
        and float(row.get("score", 0.0) or 0.0) >= 0.5 * top_score
        for row in scored_rows
    )
    weak_specificity = specificity_state in {
        "low_specificity",
        "mixed_specificity",
        "glossary_dominated",
    }
    hub_cap = limit if not competitive_non_hub_exists else (1 if scope_kind == "document" else 2)
    same_document_cap = 2 if scope_kind == "section" and weak_specificity else limit
    distinct_doc_required = scope_kind == "section" and weak_specificity
    available_documents = {
        str(row.get("document_link", ""))
        for row in scored_rows
        if str(row.get("document_link", ""))
    }
    selected: list[dict[str, Any]] = []
    selected_links: set[str] = set()
    selected_docs: dict[str, int] = {}
    selected_hubs = 0
    relax_same_document = False
    relax_hub_cap = False
    relax_distinct_docs = False
    while len(selected) < limit:
        picked = False
        for row in rows:
            link = str(row.get("link", ""))
            if link in selected_links:
                continue
            content_type = str(row.get("content_type", "unknown"))
            document_link = str(row.get("document_link", ""))
            if content_type in HUB_CONTENT_TYPES and not relax_hub_cap and selected_hubs >= hub_cap:
                continue
            if (
                scope_kind == "section"
                and not relax_same_document
                and selected_docs.get(document_link, 0) >= same_document_cap
            ):
                continue
            if (
                distinct_doc_required
                and not relax_distinct_docs
                and len(selected) < 2
                and selected_docs
                and document_link in selected_docs
            ):
                continue
            selected.append(row)
            selected_links.add(link)
            if content_type in HUB_CONTENT_TYPES:
                selected_hubs += 1
            if document_link:
                selected_docs[document_link] = selected_docs.get(document_link, 0) + 1
            picked = True
            break
        if picked:
            continue
        if scope_kind == "section" and weak_specificity and not relax_same_document:
            relax_same_document = True
            continue
        if not relax_hub_cap:
            relax_hub_cap = True
            continue
        if distinct_doc_required and not relax_distinct_docs:
            relax_distinct_docs = True
            continue
        break
    if needs_normative and not any(
        str(row.get("content_type", "unknown")) == "normative" for row in selected
    ):
        best_normative = next(
            (
                row
                for row in sorted(scored_rows, key=_row_sort_key)
                if str(row.get("content_type", "unknown")) == "normative"
            ),
            None,
        )
        if best_normative is not None:
            selected = [best_normative] + [
                row
                for row in selected
                if str(row.get("link", "")) != str(best_normative.get("link", ""))
            ]
            selected = selected[:limit]
    if distinct_doc_required and len(available_documents) >= 2:
        selected_doc_links = {
            str(row.get("document_link", ""))
            for row in selected
            if str(row.get("document_link", ""))
        }
        if len(selected_doc_links) < 2:
            alternate = next(
                (
                    row
                    for row in sorted(scored_rows, key=_row_sort_key)
                    if str(row.get("document_link", "")) not in selected_doc_links
                ),
                None,
            )
            if alternate is not None:
                selected = ([alternate] + selected)[:limit]
    selected = sorted(_unique_preserve(selected, limit=limit), key=_row_sort_key)
    return selected[:limit]


def _finalize_prior_rows(
    rows: list[dict[str, Any]],
    *,
    link_key: str,
    specificity_state: str,
    packet_health: dict[str, Any],
) -> list[dict[str, Any]]:
    finalized: list[dict[str, Any]] = []
    for row in rows:
        evidence = dict(_as_dict(row.get("evidence")))
        evidence["prior_health_snapshot"] = dict(packet_health)
        finalized.append(
            {
                link_key: str(row.get(link_key, row.get("link", ""))),
                "score": round(float(row.get("score", 0.0) or 0.0), 6),
                "content_type": str(row.get("content_type", "unknown")),
                "specificity_state": specificity_state,
                "evidence": evidence,
            }
        )
    return finalized


def _prior_documents(
    connection: sqlite3.Connection, grounding: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    phrase_records = _phrase_records(grounding)
    token_records = _token_records(grounding)
    code_records = _code_records(grounding)
    structural_signals = _structural_signals(phrase_records, token_records, code_records)
    evidence_tokens = _unique_preserve(
        [
            str(record.get("token", ""))
            for record in token_records
            if not bool(record.get("generic", False))
        ]
        + [
            str(record.get("token", ""))
            for record in code_records
            if str(record.get("kind", "")) == "api_symbol"
        ]
    )
    role_counts = _role_feature_counts_by_document(
        connection, [str(token) for token in evidence_tokens]
    )
    scored = [
        _compute_prior_feature_row(
            candidate_row,
            phrase_records=phrase_records,
            token_records=token_records,
            code_records=code_records,
            structural_signals=structural_signals,
            role_counts=dict(role_counts.get(str(candidate_row.get("document_link", "")), {})),
        )
        for candidate_row in _document_candidate_rows(connection)
    ]
    scored = [
        row
        for row in sorted(scored, key=_row_sort_key)
        if float(row.get("score", 0.0) or 0.0) > 0.0
    ]
    return scored, _select_diversified_priors(
        scored, MAX_DOCUMENT_PRIORS, "document", "high_specificity"
    )


def _prior_sections(
    connection: sqlite3.Connection, grounding: dict[str, Any], specificity_state: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    phrase_records = _phrase_records(grounding)
    token_records = _token_records(grounding)
    code_records = _code_records(grounding)
    structural_signals = _structural_signals(phrase_records, token_records, code_records)
    evidence_tokens = _unique_preserve(
        [
            str(record.get("token", ""))
            for record in token_records
            if not bool(record.get("generic", False))
        ]
        + [
            str(record.get("token", ""))
            for record in code_records
            if str(record.get("kind", "")) == "api_symbol"
        ]
    )
    role_counts = _role_feature_counts_by_section(
        connection, [str(token) for token in evidence_tokens]
    )
    scored = [
        _compute_prior_feature_row(
            candidate_row,
            phrase_records=phrase_records,
            token_records=token_records,
            code_records=code_records,
            structural_signals=structural_signals,
            role_counts=dict(role_counts.get(str(candidate_row.get("section_link", "")), {})),
        )
        for candidate_row in _section_candidate_rows(connection)
    ]
    scored = [
        row
        for row in sorted(scored, key=_row_sort_key)
        if float(row.get("score", 0.0) or 0.0) > 0.0
    ]
    return scored, _select_diversified_priors(
        scored, MAX_SECTION_PRIORS, "section", specificity_state
    )


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
    packet_health: dict[str, Any] = {
        "specificity_state": "high_specificity",
        "specificity_reasons": [],
        "generic_token_ratio": 0.0,
        "distinctive_phrase_count": 0,
        "phrase_coverage_ratio": 0.0,
        "top_prior_margin": 0.0,
        "glossary_share": 0.0,
        "hub_share": 0.0,
        "normative_share": 0.0,
    }
    if resolved_db_path.exists():
        with sqlite3.connect(resolved_db_path) as connection:
            phrase_records = _phrase_records(artifact)
            token_records = _token_records(artifact)
            scored_document_rows, _ = _prior_documents(connection, artifact)
            packet_health = _compute_packet_specificity_state(
                scored_document_rows,
                [],
                phrase_records,
                token_records,
            )
            scored_section_rows, selected_section_rows = _prior_sections(
                connection,
                artifact,
                str(packet_health["specificity_state"]),
            )
            packet_health = _compute_packet_specificity_state(
                scored_document_rows,
                scored_section_rows,
                phrase_records,
                token_records,
            )
            selected_document_rows = _select_diversified_priors(
                scored_document_rows,
                MAX_DOCUMENT_PRIORS,
                "document",
                str(packet_health["specificity_state"]),
            )
            selected_section_rows = _select_diversified_priors(
                scored_section_rows,
                MAX_SECTION_PRIORS,
                "section",
                str(packet_health["specificity_state"]),
            )
            artifact["prior_documents"] = _finalize_prior_rows(
                selected_document_rows,
                link_key="document_link",
                specificity_state=str(packet_health["specificity_state"]),
                packet_health=packet_health,
            )
            artifact["prior_sections"] = _finalize_prior_rows(
                selected_section_rows,
                link_key="section_link",
                specificity_state=str(packet_health["specificity_state"]),
                packet_health=packet_health,
            )
    if not artifact["prior_documents"]:
        ambiguity_notes.append("broad_document_priors")
    if not artifact["prior_sections"]:
        ambiguity_notes.append("broad_section_priors")
    if str(packet_health.get("specificity_state", "")) == "low_specificity":
        ambiguity_notes.append("low_specificity_priors")
    if str(packet_health.get("specificity_state", "")) == "glossary_dominated":
        ambiguity_notes.append("glossary_dominated_priors")
    if float(packet_health.get("hub_share", 0.0) or 0.0) >= 0.6:
        ambiguity_notes.append("hub_heavy_priors")
    artifact["ambiguity_notes"] = _unique_preserve(ambiguity_notes)
    return artifact
