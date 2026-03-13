from __future__ import annotations

import hashlib
import re
from typing import Any

from retrieval.build.reference_parsing import StatementRecord

SEMANTIC_TOKEN_RE = re.compile(r"[a-z0-9_]+")

ROW_MARKER_FAMILY_HINTS: dict[str, tuple[str, ...]] = {
    "1a": ("abstraction", "typing"),
    "1b": ("control-flow", "defensive"),
    "1c": ("typing",),
    "1d": ("defensive", "control-flow"),
    "1e": ("memory-safety", "typing"),
    "1f": ("unsafe", "diagnostics"),
    "1g": ("abstraction", "diagnostics"),
    "1h": ("concurrency", "memory-safety"),
    "1i": ("defensive", "diagnostics"),
}

ROW_REQUIREMENT_PATTERNS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"strong\s+typing", re.IGNORECASE), ("typing",)),
    (re.compile(r"defensive\s+programming", re.IGNORECASE), ("defensive", "control-flow")),
    (re.compile(r"concurrency", re.IGNORECASE), ("concurrency", "memory-safety")),
    (re.compile(r"verification|testing", re.IGNORECASE), ("diagnostics", "control-flow")),
    (re.compile(r"language\s+subset", re.IGNORECASE), ("diagnostics", "typing")),
    (re.compile(r"modelling|modeling", re.IGNORECASE), ("abstraction", "typing")),
)

FAMILY_TO_CONCEPT: dict[str, str] = {
    "typing": "typing",
    "abstraction": "abstraction",
    "control-flow": "control_flow",
    "defensive": "defensive",
    "memory-safety": "memory_safety",
    "concurrency": "concurrency",
    "unsafe": "unsafe",
    "diagnostics": "diagnostics",
}

SEMANTIC_CONCEPT_TERMS: dict[str, tuple[str, ...]] = {
    "typing": (
        "type",
        "typed",
        "struct",
        "enum",
        "newtype",
        "tuple",
        "generic",
        "signature",
        "compile",
        "alias",
        "invariant",
        "wrapper",
    ),
    "abstraction": (
        "trait",
        "interface",
        "contract",
        "abstraction",
        "architecture",
        "model",
        "modeling",
    ),
    "control_flow": (
        "match",
        "pattern",
        "exhaustive",
        "branch",
        "condition",
        "path",
        "flow",
    ),
    "defensive": (
        "result",
        "option",
        "error",
        "guard",
        "fallback",
        "panic",
        "recover",
    ),
    "memory_safety": (
        "ownership",
        "borrow",
        "lifetime",
        "reference",
        "alias",
        "unsafe",
    ),
    "concurrency": (
        "thread",
        "send",
        "sync",
        "mutex",
        "lock",
        "race",
        "atomic",
    ),
    "unsafe": (
        "unsafe",
        "invariant",
        "precondition",
        "postcondition",
        "proof",
        "safety",
    ),
    "diagnostics": (
        "lint",
        "attribute",
        "warning",
        "deny",
        "diagnostic",
        "diagnostics",
        "check",
    ),
    "verification": ("verify", "verification", "test", "testing", "proof"),
    "language_subset": (
        "subset",
        "profile",
        "restriction",
        "allowed",
        "forbidden",
    ),
    "modeling": ("model", "modeling", "modelling", "architecture", "representation"),
}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_semantic_text(value: str) -> str:
    return " ".join(str(value).split())


def _semantic_tokens(value: str) -> set[str]:
    normalized = _normalize_semantic_text(value).lower()
    return set(SEMANTIC_TOKEN_RE.findall(normalized))


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    if intersection == 0:
        return 0.0
    return intersection / float(len(left | right))


def _resolve_row_families(row_marker: str, requirement_text: str) -> list[str]:
    families: list[str] = list(ROW_MARKER_FAMILY_HINTS.get(row_marker, ()))
    for pattern, matched_families in ROW_REQUIREMENT_PATTERNS:
        if pattern.search(requirement_text):
            for family in matched_families:
                if family not in families:
                    families.append(family)
    if not families:
        families.append("typing")
    return families


def build_semantic_models(
    source_fetched_at: str,
    retrieval_mode: str,
    embedding_model_id: str,
    embedding_model_revision: str,
    embedding_model_license: str,
    embedding_dim: int,
    reranker_model_id: str,
    reranker_model_revision: str,
    reranker_model_license: str,
) -> list[dict[str, Any]]:
    return [
        {
            "model_id": "semantic.embedder.primary",
            "model_role": "embedder",
            "model_name": embedding_model_id,
            "model_revision": embedding_model_revision,
            "embedding_dim": int(embedding_dim),
            "distance_metric": "cosine",
            "license": embedding_model_license,
            "provider": "huggingface-tei",
            "retrieval_mode": retrieval_mode,
            "created_at": source_fetched_at,
        },
        {
            "model_id": "semantic.reranker.primary",
            "model_role": "reranker",
            "model_name": reranker_model_id,
            "model_revision": reranker_model_revision,
            "embedding_dim": 0,
            "distance_metric": "n/a",
            "license": reranker_model_license,
            "provider": "huggingface-tei",
            "retrieval_mode": retrieval_mode,
            "created_at": source_fetched_at,
        },
    ]


def build_semantic_corpus(
    table_rows: list[dict[str, Any]],
    mechanisms: list[dict[str, Any]],
    mechanism_evidence: list[dict[str, Any]],
    statements: list[StatementRecord],
    source_fetched_at: str,
    reference_source_url: str,
) -> list[dict[str, Any]]:
    statements_by_id = {statement.statement_id: statement for statement in statements}
    evidence_by_mechanism: dict[str, list[dict[str, Any]]] = {}
    for evidence in mechanism_evidence:
        evidence_by_mechanism.setdefault(str(evidence["mechanism_id"]), []).append(evidence)

    for mechanism_id in evidence_by_mechanism:
        evidence_by_mechanism[mechanism_id].sort(
            key=lambda row: (
                -float(row.get("confidence", 0.0)),
                str(row.get("evidence_id", "")),
            )
        )

    semantic_corpus: list[dict[str, Any]] = []

    for row in sorted(table_rows, key=lambda item: item["row_marker"]):
        text = _normalize_semantic_text(
            f"Table 1 {row['row_marker']} requirement. {row['requirement_text']}"
        )
        row_anchor = f"{reference_source_url}#iso26262-table1-{row['row_marker']}"
        semantic_corpus.append(
            {
                "corpus_id": f"row::{row['row_node_id']}",
                "source_kind": "table1_row",
                "source_id": row["row_node_id"],
                "row_node_id": row["row_node_id"],
                "mechanism_id": "",
                "source_anchor": row_anchor,
                "text": text,
                "text_sha256": _sha256_text(text.lower()),
                "source_fetched_at": source_fetched_at,
            }
        )

    for mechanism in sorted(mechanisms, key=lambda item: item["mechanism_id"]):
        mechanism_id = str(mechanism["mechanism_id"])
        evidence_rows = evidence_by_mechanism.get(mechanism_id, [])
        excerpts: list[str] = []
        source_anchor = reference_source_url
        if evidence_rows:
            source_anchor = str(evidence_rows[0].get("source_anchor", source_anchor))
        for evidence in evidence_rows[:3]:
            statement_id = str(evidence.get("statement_id") or "").strip()
            if statement_id and statement_id in statements_by_id:
                excerpts.append(statements_by_id[statement_id].text)
            else:
                excerpts.append(str(evidence.get("text_excerpt", "")).strip())

        profile_text = _normalize_semantic_text(
            " ".join(
                [
                    f"Mechanism {mechanism_id}.",
                    f"Symbol {mechanism['canonical_symbol']}.",
                    f"Family {mechanism['mechanism_family']}.",
                    f"Enforcement {mechanism['enforcement_kind']}.",
                    f"Stability {mechanism['stability']}.",
                    " ".join(value for value in excerpts if value),
                ]
            )
        )
        semantic_corpus.append(
            {
                "corpus_id": f"mechanism::{mechanism_id}",
                "source_kind": "mechanism_profile",
                "source_id": mechanism_id,
                "row_node_id": "",
                "mechanism_id": mechanism_id,
                "source_anchor": source_anchor,
                "text": profile_text,
                "text_sha256": _sha256_text(profile_text.lower()),
                "source_fetched_at": source_fetched_at,
            }
        )

    for evidence in mechanism_evidence:
        statement_id = str(evidence.get("statement_id") or "").strip()
        if statement_id and statement_id in statements_by_id:
            text = statements_by_id[statement_id].text
            source_id = statement_id
        else:
            text = str(evidence.get("text_excerpt", "")).strip()
            source_id = str(evidence.get("evidence_id", "")).strip()

        normalized = _normalize_semantic_text(text)
        semantic_corpus.append(
            {
                "corpus_id": f"evidence::{evidence['evidence_id']}",
                "source_kind": "statement_evidence",
                "source_id": source_id,
                "row_node_id": "",
                "mechanism_id": str(evidence["mechanism_id"]),
                "source_anchor": str(evidence.get("source_anchor", reference_source_url)),
                "text": normalized,
                "text_sha256": _sha256_text(normalized.lower()),
                "source_fetched_at": source_fetched_at,
            }
        )

    return semantic_corpus


def build_row_queryability(
    table_rows: list[dict[str, Any]],
    mechanisms: list[dict[str, Any]],
    mechanism_evidence: list[dict[str, Any]],
    statements: list[StatementRecord],
    evidence_count_by_mechanism: dict[str, int],
    best_anchor_by_mechanism: dict[str, dict[str, Any]],
    source_fetched_at: str,
    retrieval_mode: str,
    semantic_profile_version: str,
    reference_source_url: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    statements_by_id = {statement.statement_id: statement for statement in statements}
    evidence_by_mechanism: dict[str, list[dict[str, Any]]] = {}
    for evidence in mechanism_evidence:
        evidence_by_mechanism.setdefault(str(evidence["mechanism_id"]), []).append(evidence)

    for mechanism_id in evidence_by_mechanism:
        evidence_by_mechanism[mechanism_id].sort(
            key=lambda row: (
                -float(row.get("confidence", 0.0)),
                str(row.get("evidence_id", "")),
            )
        )

    row_verdicts: list[dict[str, Any]] = []
    row_mechanisms: list[dict[str, Any]] = []
    row_mechanism_scores: list[dict[str, Any]] = []
    applicable = 0
    not_applicable = 0

    for row in sorted(table_rows, key=lambda item: item["row_marker"]):
        families = _resolve_row_families(row["row_marker"], row["requirement_text"])
        row_tokens = _semantic_tokens(row["requirement_text"])
        for family in families:
            concept = FAMILY_TO_CONCEPT.get(family, family.replace("-", "_"))
            for term in SEMANTIC_CONCEPT_TERMS.get(concept, ()):  # semantic expansion seed
                row_tokens.update(_semantic_tokens(term))

        if not row_tokens:
            row_tokens = _semantic_tokens(row["row_marker"])

        candidates: list[dict[str, Any]] = []
        for mechanism in mechanisms:
            mechanism_id = str(mechanism["mechanism_id"])
            family_match = mechanism["mechanism_family"] in families
            evidence_rows = evidence_by_mechanism.get(mechanism_id, [])
            evidence_count = int(evidence_count_by_mechanism.get(mechanism_id, 0))

            lexical_score = (1.0 if family_match else 0.0) + min(evidence_count / 20.0, 1.0) * 0.25

            best_semantic_score = 0.0
            top_statement_id = ""
            top_anchor = str(
                best_anchor_by_mechanism.get(mechanism_id, {}).get(
                    "source_anchor", reference_source_url
                )
            )
            top_section_id = str(
                best_anchor_by_mechanism.get(mechanism_id, {}).get("section_id", "")
            )

            for evidence in evidence_rows:
                statement_id = str(evidence.get("statement_id") or "").strip()
                if statement_id and statement_id in statements_by_id:
                    candidate_text = statements_by_id[statement_id].text
                else:
                    candidate_text = str(evidence.get("text_excerpt", "")).strip()
                semantic_score = _jaccard_similarity(row_tokens, _semantic_tokens(candidate_text))
                if semantic_score > best_semantic_score:
                    best_semantic_score = semantic_score
                    top_statement_id = statement_id
                    top_anchor = str(evidence.get("source_anchor", top_anchor))
                    top_section_id = str(evidence.get("section_id", top_section_id))

            mechanism_tokens = _semantic_tokens(
                f"{mechanism['canonical_symbol']} {mechanism['mechanism_family']}"
            )
            best_semantic_score = max(
                best_semantic_score,
                _jaccard_similarity(row_tokens, mechanism_tokens),
            )
            if family_match:
                best_semantic_score = min(1.0, best_semantic_score + 0.12)

            reranker_score = min(
                1.0,
                (0.75 * best_semantic_score) + (0.25 * (1.0 if family_match else 0.0)),
            )
            if retrieval_mode == "lexical":
                hybrid_score = lexical_score
            else:
                hybrid_score = (
                    (0.20 * lexical_score) + (0.45 * best_semantic_score) + (0.35 * reranker_score)
                )

            candidates.append(
                {
                    "mechanism_id": mechanism_id,
                    "mechanism_family": mechanism["mechanism_family"],
                    "lexical_score": lexical_score,
                    "semantic_score": best_semantic_score,
                    "reranker_score": reranker_score,
                    "hybrid_score": hybrid_score,
                    "top_statement_id": top_statement_id,
                    "top_anchor": top_anchor,
                    "top_section_id": top_section_id,
                    "family_match": family_match,
                }
            )

        candidates.sort(
            key=lambda value: (
                -float(value["hybrid_score"]),
                -float(value["semantic_score"]),
                str(value["mechanism_id"]),
            )
        )

        selected: list[dict[str, Any]] = [
            candidate for candidate in candidates if candidate["family_match"]
        ]
        if not selected:
            selected = candidates[:3]

        if selected:
            applicable += 1
            top = selected[0]
            rationale_anchor = str(top["top_anchor"])
            rationale = (
                "Rust Reference sections provide language semantics relevant to "
                f"ISO 26262 Part 6 Table 1 item {row['row_marker']} "
                f"({', '.join(families)}), ranked with {retrieval_mode} retrieval profile."
            )
            row_verdicts.append(
                {
                    "row_node_id": row["row_node_id"],
                    "verdict": "applicable",
                    "rationale": rationale,
                    "rationale_anchor": rationale_anchor,
                    "rationale_timestamp": source_fetched_at,
                }
            )

            for mechanism in selected:
                row_mechanisms.append(
                    {
                        "row_node_id": row["row_node_id"],
                        "mechanism_id": mechanism["mechanism_id"],
                        "relevance_score": round(float(mechanism["hybrid_score"]), 6),
                        "evidence_anchor": str(mechanism["top_anchor"]),
                        "evidence_section_id": str(mechanism["top_section_id"]),
                        "evidence_statement_id": str(mechanism["top_statement_id"]),
                        "source_fetched_at": source_fetched_at,
                    }
                )
                row_mechanism_scores.append(
                    {
                        "row_node_id": row["row_node_id"],
                        "mechanism_id": mechanism["mechanism_id"],
                        "lexical_score": round(float(mechanism["lexical_score"]), 6),
                        "semantic_score": round(float(mechanism["semantic_score"]), 6),
                        "reranker_score": round(float(mechanism["reranker_score"]), 6),
                        "hybrid_score": round(float(mechanism["hybrid_score"]), 6),
                        "score_version": semantic_profile_version,
                        "top_statement_id": str(mechanism["top_statement_id"]),
                        "scored_at": source_fetched_at,
                        "source_fetched_at": source_fetched_at,
                    }
                )
        else:
            not_applicable += 1
            rationale = (
                "No qualifying Rust Reference mechanisms matched the current "
                f"family mapping for ISO 26262 Part 6 Table 1 item {row['row_marker']}."
            )
            row_verdicts.append(
                {
                    "row_node_id": row["row_node_id"],
                    "verdict": "not_applicable",
                    "rationale": rationale,
                    "rationale_anchor": reference_source_url,
                    "rationale_timestamp": source_fetched_at,
                }
            )

    return (
        row_verdicts,
        row_mechanisms,
        row_mechanism_scores,
        {"applicable": applicable, "not_applicable": not_applicable},
    )
