#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retrieval.build.cli import parse_build_args
from retrieval.build.reports import (
    load_manifest as _load_manifest,
    read_previous_snapshot_path as _read_previous_snapshot_path,
    update_manifest as _update_manifest,
    validate_rust_reference_db,
    write_row_metadata_report as _write_row_metadata_report,
    write_validation_report as _write_validation_report,
)
from retrieval.build.reference_parsing import (
    SectionRecord,
    SourceDocument,
    StatementRecord,
    SummaryEntry,
    extract_sections_and_statements as _extract_sections_and_statements,
    load_source_documents as _load_source_documents,
    parse_summary as _parse_summary,
)
from retrieval.build.mechanisms import (
    extract_mechanisms_and_evidence as _extract_mechanisms_and_evidence,
)
from retrieval.build.source_checkout import (
    resolve_reference_checkout as _resolve_reference_checkout,
)
from retrieval.core.provenance import (
    apply_pending_migrations,
    canonical_json_hash,
    compute_source_state_from_db,
    record_pipeline_run,
)
from retrieval.ingest.contracts import ChunkInput, CleanInput
from retrieval.ingest.registry import resolve_ingest_strategy

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def _resolve_default_extractor_db() -> Path:
    relative = Path(
        "personal/iso-26262-coding-standard-extraction/.cache/iso26262/iso26262_index.sqlite"
    )
    candidates = (
        Path.home() / relative,
        Path("/Users") / Path.home().name / relative,
        Path("/home") / Path.home().name / relative,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


DEFAULT_EXTRACTOR_DB = _resolve_default_extractor_db()
DEFAULT_TABLE_NODE_ID = "ISO26262-6-2018:node:table:table_1:001"
DEFAULT_REFERENCE_REPO_URL = "https://github.com/rust-lang/reference.git"
DEFAULT_REFERENCE_CACHE_DIR = ".cache/sqlite_kb/sources/rust-reference"
DEFAULT_REFERENCE_SOURCE_URL = "https://doc.rust-lang.org/reference/"
DEFAULT_EMBEDDING_MODEL_ID = "Qwen/Qwen3-Embedding-4B"
DEFAULT_EMBEDDING_MODEL_REVISION = "unspecified"
DEFAULT_EMBEDDING_MODEL_LICENSE = "unspecified"
DEFAULT_EMBEDDING_DIM = 0
DEFAULT_RERANKER_MODEL_ID = "BAAI/bge-reranker-v2-m3"
DEFAULT_RERANKER_MODEL_REVISION = "unspecified"
DEFAULT_RERANKER_MODEL_LICENSE = "unspecified"
DEFAULT_RETRIEVAL_MODE = "hybrid"
DEFAULT_SEMANTIC_PROFILE_VERSION = "semantic-hybrid-v1"
DEFAULT_RETRIEVAL_CORPUS = "chunk"
RETRIEVAL_CORPUS_VALUES = ("statement", "chunk")

HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
SEMANTIC_TOKEN_RE = re.compile(r"[a-z0-9_]+")
ADMONITION_TAG_RE = re.compile(r"\[![A-Z]+\]")
FOOTNOTE_MARKER_RE = re.compile(r"\[\^[^\]]+\]")

CLEAN_TEXT_NORMALIZER_VERSION = "clean-v1"


@dataclass(frozen=True)
class ChunkRecord:
    chunk_uid: str
    section_id: str
    raw_text: str
    clean_text: str
    char_len: int
    token_len: int
    source_sha256: str
    source_fetched_at: str
    source_commit_sha: str
    order_index: int


@dataclass(frozen=True)
class ChunkSpanRecord:
    chunk_uid: str
    source_anchor: str
    start_offset: int
    end_offset: int
    span_order: int


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

ROW_REQUIREMENT_CLEAN_OVERRIDES: dict[str, str] = {
    "1a": (
        "Use architecture-level abstractions and typed interfaces so component contracts "
        "remain explicit, analyzable, and enforceable at compile time."
    ),
    "1b": (
        "Use explicit control-flow and exhaustive branching so all safety-relevant paths "
        "are handled deterministically."
    ),
    "1c": (
        "Use strong typing with domain-specific data models to prevent invalid states "
        "and reduce integration faults."
    ),
    "1d": (
        "Use defensive error handling with explicit Result and Option paths so failures "
        "are contained and recovery behavior is defined."
    ),
    "1e": (
        "Use ownership, borrowing, and lifetime constraints to preserve memory safety "
        "and prevent aliasing violations."
    ),
    "1f": (
        "Isolate unsafe operations behind reviewed boundaries with documented invariants "
        "and verification obligations."
    ),
    "1g": (
        "Apply consistent coding-style and interface conventions to improve readability, "
        "maintainability, and static analyzability."
    ),
    "1h": (
        "Constrain concurrent behavior using Send, Sync, and explicit synchronization "
        "patterns to avoid race conditions."
    ),
    "1i": (
        "Enforce a defined language subset with diagnostics and policy checks so safety "
        "constraints remain auditable and repeatable."
    ),
}

ROW_PROFILE_TERMS: dict[str, tuple[str, ...]] = {
    "1a": ("trait", "interface", "abstraction", "architecture", "module", "contract"),
    "1b": ("match", "branch", "pattern", "exhaustive", "control-flow", "state"),
    "1c": ("type", "typing", "struct", "enum", "newtype", "invariant"),
    "1d": ("result", "option", "error", "fallback", "guard", "recover"),
    "1e": ("ownership", "borrow", "lifetime", "alias", "mutable", "reference"),
    "1f": ("unsafe", "boundary", "invariant", "review", "proof", "obligation"),
    "1g": ("style", "convention", "readability", "consistency", "analyzability", "lint"),
    "1h": ("concurrency", "thread", "send", "sync", "atomic", "race"),
    "1i": ("subset", "restriction", "diagnostic", "lint", "policy", "verification"),
}

ROW_FOOTNOTES: dict[str, tuple[str, ...]] = {
    "1f": ("Unsafe code is permitted only with documented safety invariants.",),
    "1i": ("Diagnostics and policy checks must be consistently applied in CI and release flows.",),
}

TABLE1_REQUIREMENT_LEN_MIN = 48
TABLE1_REQUIREMENT_LEN_MAX = 480

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
        "recover",
        "failure",
        "check",
        "handling",
    ),
    "memory_safety": (
        "borrow",
        "borrowing",
        "lifetime",
        "ownership",
        "reference",
        "mutable",
        "alias",
        "pointer",
        "validity",
        "move",
    ),
    "concurrency": (
        "concurrency",
        "thread",
        "send",
        "sync",
        "atomic",
        "ordering",
        "race",
        "lock",
    ),
    "unsafe": (
        "unsafe",
        "invariant",
        "boundary",
        "undefined behavior",
        "ub",
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


def _normalize_marker(raw: str | None, row_idx: int) -> str:
    if raw:
        match = re.search(r"1[a-i]", raw.lower())
        if match:
            return match.group(0)
    return f"1{chr(ord('a') + row_idx - 1)}"


def _normalize_requirement_text(raw: str) -> str:
    value = str(raw or "")
    value = FOOTNOTE_MARKER_RE.sub(" ", value)
    value = HTML_COMMENT_RE.sub(" ", value)
    value = ADMONITION_TAG_RE.sub(" ", value)
    value = " ".join(value.split())
    return value.strip()


def _resolve_table1_rows(extractor_db: Path, table_node_id: str) -> list[dict[str, Any]]:
    if not extractor_db.exists():
        raise RuntimeError(f"Extractor sqlite not found: {extractor_db}")

    query = """
        SELECT
            r.node_id AS row_node_id,
            r.row_idx AS row_idx,
            c1.text AS marker_text,
            c2.text AS requirement_text
        FROM nodes r
        LEFT JOIN nodes c1
          ON c1.table_node_id = r.table_node_id
         AND c1.node_type = 'table_cell'
         AND c1.row_idx = r.row_idx
         AND c1.col_idx = 1
        LEFT JOIN nodes c2
          ON c2.table_node_id = r.table_node_id
         AND c2.node_type = 'table_cell'
         AND c2.row_idx = r.row_idx
         AND c2.col_idx = 2
        WHERE r.table_node_id = :table_node_id
          AND r.node_type = 'table_row'
        ORDER BY r.row_idx
    """

    connection = sqlite3.connect(extractor_db)
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, {"table_node_id": table_node_id}).fetchall()
    finally:
        connection.close()

    if len(rows) != 9:
        raise RuntimeError("Expected 9 Table 1 rows from extractor")

    resolved: list[dict[str, Any]] = []
    for row in rows:
        row_idx = int(row["row_idx"])
        marker = _normalize_marker(row["marker_text"], row_idx)
        requirement_raw = str(row["requirement_text"] or "").strip()
        requirement_clean = _normalize_requirement_text(requirement_raw)
        if marker in ROW_REQUIREMENT_CLEAN_OVERRIDES:
            requirement_clean = ROW_REQUIREMENT_CLEAN_OVERRIDES[marker]

        if not requirement_clean:
            raise RuntimeError(f"Missing requirement text for row marker {marker}")

        requirement_len = len(requirement_clean)
        if not (TABLE1_REQUIREMENT_LEN_MIN <= requirement_len <= TABLE1_REQUIREMENT_LEN_MAX):
            raise RuntimeError(
                f"Row requirement text length out of range for {marker}: {requirement_len}"
            )

        profile_terms = [term.strip().lower() for term in ROW_PROFILE_TERMS.get(marker, ()) if term]
        if len(profile_terms) < 3:
            raise RuntimeError(f"Insufficient row profile terms for {marker}")

        footnotes = [note.strip() for note in ROW_FOOTNOTES.get(marker, ()) if note.strip()]
        resolved.append(
            {
                "row_node_id": str(row["row_node_id"]),
                "row_idx": row_idx,
                "row_marker": marker,
                "requirement_text": requirement_clean,
                "requirement_text_raw": requirement_raw,
                "row_profile_terms": profile_terms,
                "row_footnotes": footnotes,
            }
        )

    markers = {row["row_marker"] for row in resolved}
    expected = {f"1{chr(ord('a') + idx)}" for idx in range(9)}
    if markers != expected:
        raise RuntimeError(f"Unexpected Table 1 marker set from extractor: {sorted(markers)}")

    for row in resolved:
        if not str(row.get("requirement_text", "")).strip():
            raise RuntimeError(f"Missing clean requirement_text for {row['row_marker']}")
        if not list(row.get("row_profile_terms", [])):
            raise RuntimeError(f"Missing row_profile_terms for {row['row_marker']}")
    return resolved


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


def _build_semantic_models(
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


def _build_semantic_corpus(
    table_rows: list[dict[str, Any]],
    mechanisms: list[dict[str, Any]],
    mechanism_evidence: list[dict[str, Any]],
    statements: list[StatementRecord],
    source_fetched_at: str,
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
        row_anchor = f"{DEFAULT_REFERENCE_SOURCE_URL}#iso26262-table1-{row['row_marker']}"
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
        source_anchor = DEFAULT_REFERENCE_SOURCE_URL
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
                "source_anchor": str(evidence.get("source_anchor", DEFAULT_REFERENCE_SOURCE_URL)),
                "text": normalized,
                "text_sha256": _sha256_text(normalized.lower()),
                "source_fetched_at": source_fetched_at,
            }
        )

    return semantic_corpus


def _build_row_queryability(
    table_rows: list[dict[str, Any]],
    mechanisms: list[dict[str, Any]],
    mechanism_evidence: list[dict[str, Any]],
    statements: list[StatementRecord],
    evidence_count_by_mechanism: dict[str, int],
    best_anchor_by_mechanism: dict[str, dict[str, Any]],
    source_fetched_at: str,
    retrieval_mode: str,
    semantic_profile_version: str,
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
                    "source_anchor", DEFAULT_REFERENCE_SOURCE_URL
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
                    "rationale_anchor": DEFAULT_REFERENCE_SOURCE_URL,
                    "rationale_timestamp": source_fetched_at,
                }
            )

    return (
        row_verdicts,
        row_mechanisms,
        row_mechanism_scores,
        {"applicable": applicable, "not_applicable": not_applicable},
    )


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS snapshots (
            snapshot_id TEXT PRIMARY KEY,
            commit_sha TEXT NOT NULL,
            source_url TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            sha256 TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS kb_metadata (
            kb_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            source_revision TEXT NOT NULL,
            extractor_version TEXT NOT NULL,
            built_at TEXT NOT NULL,
            notes TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chapters (
            chapter_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            order_index INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_documents (
            document_id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            rel_path TEXT NOT NULL,
            title TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            source_commit_sha TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            UNIQUE(snapshot_id, rel_path),
            FOREIGN KEY(snapshot_id) REFERENCES snapshots(snapshot_id),
            FOREIGN KEY(chapter_id) REFERENCES chapters(chapter_id)
        );

        CREATE TABLE IF NOT EXISTS docs (
            doc_uid TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            title TEXT NOT NULL,
            revision TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            FOREIGN KEY(chapter_id) REFERENCES chapters(chapter_id)
        );

        CREATE TABLE IF NOT EXISTS sections (
            section_id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            anchor TEXT NOT NULL,
            heading TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            level INTEGER NOT NULL,
            text TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            source_commit_sha TEXT NOT NULL,
            FOREIGN KEY(snapshot_id) REFERENCES snapshots(snapshot_id),
            FOREIGN KEY(document_id) REFERENCES source_documents(document_id),
            FOREIGN KEY(chapter_id) REFERENCES chapters(chapter_id)
        );

        CREATE TABLE IF NOT EXISTS statements (
            statement_id TEXT PRIMARY KEY,
            section_id TEXT NOT NULL,
            statement_type TEXT NOT NULL
                CHECK(statement_type IN ('definition', 'constraint', 'behavior')),
            text TEXT NOT NULL,
            confidence REAL NOT NULL,
            sentence_index INTEGER NOT NULL,
            source_sha256 TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            source_commit_sha TEXT NOT NULL,
            FOREIGN KEY(section_id) REFERENCES sections(section_id)
        );

        CREATE TABLE IF NOT EXISTS chunks (
            chunk_uid TEXT PRIMARY KEY,
            section_id TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            clean_text TEXT NOT NULL,
            char_len INTEGER NOT NULL,
            token_len INTEGER NOT NULL,
            source_sha256 TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            source_commit_sha TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            FOREIGN KEY(section_id) REFERENCES sections(section_id)
        );

        CREATE TABLE IF NOT EXISTS chunk_spans (
            chunk_uid TEXT NOT NULL,
            source_anchor TEXT NOT NULL,
            start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL,
            span_order INTEGER NOT NULL,
            PRIMARY KEY (chunk_uid, span_order),
            FOREIGN KEY(chunk_uid) REFERENCES chunks(chunk_uid)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
        USING fts5(
            chunk_uid UNINDEXED,
            section_id UNINDEXED,
            section_heading,
            chunk_text,
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS statements_fts
        USING fts5(
            statement_id UNINDEXED,
            section_id UNINDEXED,
            section_heading,
            statement_text,
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE TABLE IF NOT EXISTS mechanisms (
            mechanism_id TEXT PRIMARY KEY,
            canonical_symbol TEXT NOT NULL,
            mechanism_family TEXT NOT NULL,
            enforcement_kind TEXT NOT NULL,
            stability TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS mechanism_evidence (
            evidence_id TEXT PRIMARY KEY,
            mechanism_id TEXT NOT NULL,
            section_id TEXT NOT NULL,
            statement_id TEXT,
            source_anchor TEXT NOT NULL,
            evidence_kind TEXT NOT NULL,
            text_excerpt TEXT NOT NULL,
            confidence REAL NOT NULL,
            source_fetched_at TEXT NOT NULL,
            FOREIGN KEY(mechanism_id) REFERENCES mechanisms(mechanism_id),
            FOREIGN KEY(section_id) REFERENCES sections(section_id),
            FOREIGN KEY(statement_id) REFERENCES statements(statement_id)
        );

        CREATE TABLE IF NOT EXISTS table1_rows (
            row_node_id TEXT PRIMARY KEY,
            row_idx INTEGER NOT NULL,
            row_marker TEXT NOT NULL,
            table_ref TEXT NOT NULL,
            requirement_text TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS table1_row_footnotes (
            row_node_id TEXT NOT NULL,
            footnote_order INTEGER NOT NULL,
            footnote_text TEXT NOT NULL,
            PRIMARY KEY(row_node_id, footnote_order),
            FOREIGN KEY(row_node_id) REFERENCES table1_rows(row_node_id)
        );

        CREATE TABLE IF NOT EXISTS table1_row_profile_terms (
            row_node_id TEXT NOT NULL,
            term_order INTEGER NOT NULL,
            term TEXT NOT NULL,
            term_source TEXT NOT NULL,
            PRIMARY KEY(row_node_id, term_order),
            FOREIGN KEY(row_node_id) REFERENCES table1_rows(row_node_id)
        );

        CREATE TABLE IF NOT EXISTS row_verdicts (
            row_node_id TEXT PRIMARY KEY,
            verdict TEXT NOT NULL CHECK(verdict IN ('applicable', 'not_applicable')),
            rationale TEXT NOT NULL,
            rationale_anchor TEXT NOT NULL,
            rationale_timestamp TEXT NOT NULL,
            FOREIGN KEY(row_node_id) REFERENCES table1_rows(row_node_id)
        );

        CREATE TABLE IF NOT EXISTS row_mechanisms (
            row_node_id TEXT NOT NULL,
            mechanism_id TEXT NOT NULL,
            relevance_score REAL NOT NULL,
            evidence_anchor TEXT NOT NULL,
            evidence_section_id TEXT NOT NULL,
            evidence_statement_id TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            PRIMARY KEY (row_node_id, mechanism_id),
            FOREIGN KEY(row_node_id) REFERENCES table1_rows(row_node_id),
            FOREIGN KEY(mechanism_id) REFERENCES mechanisms(mechanism_id)
        );

        CREATE TABLE IF NOT EXISTS semantic_models (
            model_id TEXT PRIMARY KEY,
            model_role TEXT NOT NULL CHECK(model_role IN ('embedder', 'reranker')),
            model_name TEXT NOT NULL,
            model_revision TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL,
            distance_metric TEXT NOT NULL,
            license TEXT NOT NULL,
            provider TEXT NOT NULL,
            retrieval_mode TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS semantic_corpus (
            corpus_id TEXT PRIMARY KEY,
            source_kind TEXT NOT NULL,
            source_id TEXT NOT NULL,
            row_node_id TEXT,
            mechanism_id TEXT,
            source_anchor TEXT NOT NULL,
            text TEXT NOT NULL,
            text_sha256 TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            FOREIGN KEY(row_node_id) REFERENCES table1_rows(row_node_id),
            FOREIGN KEY(mechanism_id) REFERENCES mechanisms(mechanism_id)
        );

        CREATE TABLE IF NOT EXISTS statement_embeddings (
            statement_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            text_sha256 TEXT NOT NULL,
            vector_json TEXT NOT NULL,
            vector_norm REAL NOT NULL,
            embedded_at TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            PRIMARY KEY(statement_id, model_id),
            FOREIGN KEY(statement_id) REFERENCES statements(statement_id)
        );

        CREATE TABLE IF NOT EXISTS chunk_embeddings (
            chunk_uid TEXT NOT NULL,
            model_id TEXT NOT NULL,
            embed_version TEXT NOT NULL,
            text_sha256 TEXT NOT NULL,
            vector_json TEXT NOT NULL,
            vector_norm REAL NOT NULL,
            embedded_at TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            PRIMARY KEY(chunk_uid, model_id, embed_version),
            FOREIGN KEY(chunk_uid) REFERENCES chunks(chunk_uid)
        );

        CREATE TABLE IF NOT EXISTS row_mechanism_scores (
            row_node_id TEXT NOT NULL,
            mechanism_id TEXT NOT NULL,
            lexical_score REAL NOT NULL,
            semantic_score REAL NOT NULL,
            reranker_score REAL NOT NULL,
            hybrid_score REAL NOT NULL,
            score_version TEXT NOT NULL,
            top_statement_id TEXT,
            scored_at TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            PRIMARY KEY (row_node_id, mechanism_id),
            FOREIGN KEY(row_node_id) REFERENCES table1_rows(row_node_id),
            FOREIGN KEY(mechanism_id) REFERENCES mechanisms(mechanism_id),
            FOREIGN KEY(top_statement_id) REFERENCES statements(statement_id)
        );

        CREATE INDEX IF NOT EXISTS idx_chapters_order ON chapters(order_index);
        CREATE INDEX IF NOT EXISTS idx_documents_chapter
            ON source_documents(chapter_id, order_index);
        CREATE INDEX IF NOT EXISTS idx_docs_chapter
            ON docs(chapter_id, order_index);
        CREATE INDEX IF NOT EXISTS idx_sections_document ON sections(document_id, order_index);
        CREATE INDEX IF NOT EXISTS idx_sections_chapter ON sections(chapter_id, order_index);
        CREATE INDEX IF NOT EXISTS idx_statements_section ON statements(section_id, sentence_index);
        CREATE INDEX IF NOT EXISTS idx_chunks_section ON chunks(section_id, order_index);
        CREATE INDEX IF NOT EXISTS idx_chunk_spans_anchor ON chunk_spans(source_anchor, chunk_uid);
        CREATE INDEX IF NOT EXISTS idx_mechanism_evidence_mech ON mechanism_evidence(mechanism_id);
        CREATE INDEX IF NOT EXISTS idx_table1_rows_marker ON table1_rows(row_marker);
        CREATE INDEX IF NOT EXISTS idx_table1_row_footnotes_row
            ON table1_row_footnotes(row_node_id, footnote_order);
        CREATE INDEX IF NOT EXISTS idx_table1_row_profile_terms_row
            ON table1_row_profile_terms(row_node_id, term_order);
        CREATE INDEX IF NOT EXISTS idx_row_mechanisms_row ON row_mechanisms(row_node_id);
        CREATE INDEX IF NOT EXISTS idx_semantic_corpus_source
            ON semantic_corpus(source_kind, source_id);
        CREATE INDEX IF NOT EXISTS idx_semantic_corpus_mechanism
            ON semantic_corpus(mechanism_id, source_kind);
        CREATE INDEX IF NOT EXISTS idx_statement_embeddings_model
            ON statement_embeddings(model_id, statement_id);
        CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_model
            ON chunk_embeddings(model_id, chunk_uid, embed_version);
        CREATE INDEX IF NOT EXISTS idx_row_mechanism_scores_row
            ON row_mechanism_scores(row_node_id, hybrid_score DESC);

        PRAGMA user_version = 6;
        """
    )


def _compute_snapshot_sha256(
    commit_sha: str,
    documents: list[SourceDocument],
    sections: list[SectionRecord],
    statements: list[StatementRecord],
    chunks: list[ChunkRecord],
) -> str:
    payload = {
        "commit_sha": commit_sha,
        "document_hashes": sorted((doc.rel_path, doc.source_sha256) for doc in documents),
        "sections": len(sections),
        "statements": len(statements),
        "chunks": len(chunks),
    }
    return _sha256_text(json.dumps(payload, sort_keys=True))


def _insert_payload(
    connection: sqlite3.Connection,
    snapshot_id: str,
    commit_sha: str,
    fetched_at: str,
    snapshot_sha256: str,
    chapters: list[dict[str, Any]],
    documents: list[SourceDocument],
    sections: list[SectionRecord],
    statements: list[StatementRecord],
    chunks: list[ChunkRecord],
    chunk_spans: list[ChunkSpanRecord],
    mechanisms: list[dict[str, Any]],
    mechanism_evidence: list[dict[str, Any]],
    table_rows: list[dict[str, Any]],
    row_verdicts: list[dict[str, Any]],
    row_mechanisms: list[dict[str, Any]],
    semantic_models: list[dict[str, Any]],
    semantic_corpus: list[dict[str, Any]],
    row_mechanism_scores: list[dict[str, Any]],
    extractor_version: str,
    build_notes: str,
) -> None:
    section_heading_by_id = {section.section_id: section.heading for section in sections}

    connection.execute(
        """
        INSERT INTO snapshots(snapshot_id, commit_sha, source_url, fetched_at, sha256)
        VALUES(?, ?, ?, ?, ?)
        """,
        (snapshot_id, commit_sha, DEFAULT_REFERENCE_SOURCE_URL, fetched_at, snapshot_sha256),
    )
    connection.execute(
        """
        INSERT INTO kb_metadata(
            kb_id,
            source_name,
            source_revision,
            extractor_version,
            built_at,
            notes
        ) VALUES(?, ?, ?, ?, ?, ?)
        """,
        (
            "rust_reference",
            "rust-reference",
            commit_sha,
            extractor_version,
            fetched_at,
            build_notes,
        ),
    )

    for chapter in chapters:
        connection.execute(
            "INSERT INTO chapters(chapter_id, title, order_index) VALUES(?, ?, ?)",
            (chapter["chapter_id"], chapter["title"], int(chapter["order_index"])),
        )

    for document in documents:
        connection.execute(
            """
            INSERT INTO source_documents(
                document_id,
                snapshot_id,
                chapter_id,
                rel_path,
                title,
                source_sha256,
                source_fetched_at,
                source_commit_sha,
                order_index
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.document_id,
                snapshot_id,
                document.chapter_id,
                document.rel_path,
                document.title,
                document.source_sha256,
                document.source_fetched_at,
                document.source_commit_sha,
                document.doc_order,
            ),
        )
        connection.execute(
            """
            INSERT INTO docs(
                doc_uid,
                source_path,
                title,
                revision,
                fetched_at,
                source_sha256,
                chapter_id,
                order_index
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.document_id,
                document.rel_path,
                document.title,
                document.source_commit_sha,
                document.source_fetched_at,
                document.source_sha256,
                document.chapter_id,
                document.doc_order,
            ),
        )

    for section in sections:
        connection.execute(
            """
            INSERT INTO sections(
                section_id,
                snapshot_id,
                document_id,
                chapter_id,
                anchor,
                heading,
                order_index,
                level,
                text,
                source_sha256,
                source_fetched_at,
                source_commit_sha
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                section.section_id,
                section.snapshot_id,
                section.document_id,
                section.chapter_id,
                section.anchor,
                section.heading,
                section.order_index,
                section.level,
                section.text,
                section.source_sha256,
                section.source_fetched_at,
                section.source_commit_sha,
            ),
        )

    for statement in statements:
        connection.execute(
            """
            INSERT INTO statements(
                statement_id,
                section_id,
                statement_type,
                text,
                confidence,
                sentence_index,
                source_sha256,
                source_fetched_at,
                source_commit_sha
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                statement.statement_id,
                statement.section_id,
                statement.statement_type,
                statement.text,
                statement.confidence,
                statement.sentence_index,
                statement.source_sha256,
                statement.source_fetched_at,
                statement.source_commit_sha,
            ),
        )
        connection.execute(
            """
            INSERT INTO statements_fts(
                statement_id,
                section_id,
                section_heading,
                statement_text
            ) VALUES(?, ?, ?, ?)
            """,
            (
                statement.statement_id,
                statement.section_id,
                section_heading_by_id.get(statement.section_id, ""),
                statement.text,
            ),
        )

    for chunk in chunks:
        connection.execute(
            """
            INSERT INTO chunks(
                chunk_uid,
                section_id,
                raw_text,
                clean_text,
                char_len,
                token_len,
                source_sha256,
                source_fetched_at,
                source_commit_sha,
                order_index
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk.chunk_uid,
                chunk.section_id,
                chunk.raw_text,
                chunk.clean_text,
                chunk.char_len,
                chunk.token_len,
                chunk.source_sha256,
                chunk.source_fetched_at,
                chunk.source_commit_sha,
                chunk.order_index,
            ),
        )
        connection.execute(
            """
            INSERT INTO chunks_fts(
                chunk_uid,
                section_id,
                section_heading,
                chunk_text
            ) VALUES(?, ?, ?, ?)
            """,
            (
                chunk.chunk_uid,
                chunk.section_id,
                section_heading_by_id.get(chunk.section_id, ""),
                chunk.clean_text,
            ),
        )

    for chunk_span in chunk_spans:
        connection.execute(
            """
            INSERT INTO chunk_spans(
                chunk_uid,
                source_anchor,
                start_offset,
                end_offset,
                span_order
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                chunk_span.chunk_uid,
                chunk_span.source_anchor,
                chunk_span.start_offset,
                chunk_span.end_offset,
                chunk_span.span_order,
            ),
        )

    for mechanism in mechanisms:
        connection.execute(
            """
            INSERT INTO mechanisms(
                mechanism_id,
                canonical_symbol,
                mechanism_family,
                enforcement_kind,
                stability
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                mechanism["mechanism_id"],
                mechanism["canonical_symbol"],
                mechanism["mechanism_family"],
                mechanism["enforcement_kind"],
                mechanism["stability"],
            ),
        )

    for evidence in mechanism_evidence:
        connection.execute(
            """
            INSERT INTO mechanism_evidence(
                evidence_id,
                mechanism_id,
                section_id,
                statement_id,
                source_anchor,
                evidence_kind,
                text_excerpt,
                confidence,
                source_fetched_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence["evidence_id"],
                evidence["mechanism_id"],
                evidence["section_id"],
                evidence["statement_id"],
                evidence["source_anchor"],
                evidence["evidence_kind"],
                evidence["text_excerpt"],
                evidence["confidence"],
                evidence["source_fetched_at"],
            ),
        )

    for row in table_rows:
        connection.execute(
            """
            INSERT INTO table1_rows(row_node_id, row_idx, row_marker, table_ref, requirement_text)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                row["row_node_id"],
                int(row["row_idx"]),
                row["row_marker"],
                "ISO26262-6-2018 Table 1",
                row["requirement_text"],
            ),
        )
        for footnote_order, footnote_text in enumerate(row.get("row_footnotes", []), start=1):
            connection.execute(
                """
                INSERT INTO table1_row_footnotes(row_node_id, footnote_order, footnote_text)
                VALUES(?, ?, ?)
                """,
                (
                    row["row_node_id"],
                    int(footnote_order),
                    str(footnote_text),
                ),
            )
        for term_order, term in enumerate(row.get("row_profile_terms", []), start=1):
            connection.execute(
                """
                INSERT INTO table1_row_profile_terms(row_node_id, term_order, term, term_source)
                VALUES(?, ?, ?, ?)
                """,
                (
                    row["row_node_id"],
                    int(term_order),
                    str(term),
                    "curated",
                ),
            )

    for verdict in row_verdicts:
        connection.execute(
            """
            INSERT INTO row_verdicts(
                row_node_id,
                verdict,
                rationale,
                rationale_anchor,
                rationale_timestamp
            )
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                verdict["row_node_id"],
                verdict["verdict"],
                verdict["rationale"],
                verdict["rationale_anchor"],
                verdict["rationale_timestamp"],
            ),
        )

    for row_mechanism in row_mechanisms:
        connection.execute(
            """
            INSERT INTO row_mechanisms(
                row_node_id,
                mechanism_id,
                relevance_score,
                evidence_anchor,
                evidence_section_id,
                evidence_statement_id,
                source_fetched_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_mechanism["row_node_id"],
                row_mechanism["mechanism_id"],
                row_mechanism["relevance_score"],
                row_mechanism["evidence_anchor"],
                row_mechanism["evidence_section_id"],
                row_mechanism["evidence_statement_id"],
                row_mechanism["source_fetched_at"],
            ),
        )

    for model in semantic_models:
        connection.execute(
            """
            INSERT INTO semantic_models(
                model_id,
                model_role,
                model_name,
                model_revision,
                embedding_dim,
                distance_metric,
                license,
                provider,
                retrieval_mode,
                created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model["model_id"],
                model["model_role"],
                model["model_name"],
                model["model_revision"],
                int(model["embedding_dim"]),
                model["distance_metric"],
                model["license"],
                model["provider"],
                model["retrieval_mode"],
                model["created_at"],
            ),
        )

    for corpus_row in semantic_corpus:
        connection.execute(
            """
            INSERT INTO semantic_corpus(
                corpus_id,
                source_kind,
                source_id,
                row_node_id,
                mechanism_id,
                source_anchor,
                text,
                text_sha256,
                source_fetched_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corpus_row["corpus_id"],
                corpus_row["source_kind"],
                corpus_row["source_id"],
                corpus_row["row_node_id"] or None,
                corpus_row["mechanism_id"] or None,
                corpus_row["source_anchor"],
                corpus_row["text"],
                corpus_row["text_sha256"],
                corpus_row["source_fetched_at"],
            ),
        )

    for score_row in row_mechanism_scores:
        connection.execute(
            """
            INSERT INTO row_mechanism_scores(
                row_node_id,
                mechanism_id,
                lexical_score,
                semantic_score,
                reranker_score,
                hybrid_score,
                score_version,
                top_statement_id,
                scored_at,
                source_fetched_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                score_row["row_node_id"],
                score_row["mechanism_id"],
                float(score_row["lexical_score"]),
                float(score_row["semantic_score"]),
                float(score_row["reranker_score"]),
                float(score_row["hybrid_score"]),
                score_row["score_version"],
                score_row["top_statement_id"] or None,
                score_row["scored_at"],
                score_row["source_fetched_at"],
            ),
        )


def build_rust_reference_db(
    db_path: Path,
    snapshot_root: Path,
    manifest_path: Path,
    extractor_db: Path,
    table_node_id: str,
    reference_source_dir: Path | None = None,
    reference_cache_dir: Path | None = None,
    reference_repo_url: str = DEFAULT_REFERENCE_REPO_URL,
    reference_revision: str | None = None,
    skip_fetch: bool = False,
    report_root: Path | None = None,
    min_sections: int = 20,
    min_statements: int = 50,
    min_mechanisms: int = 6,
    retrieval_mode: str = DEFAULT_RETRIEVAL_MODE,
    retrieval_corpus: str = DEFAULT_RETRIEVAL_CORPUS,
    semantic_profile_version: str = DEFAULT_SEMANTIC_PROFILE_VERSION,
    embedding_model_id: str = DEFAULT_EMBEDDING_MODEL_ID,
    embedding_model_revision: str = DEFAULT_EMBEDDING_MODEL_REVISION,
    embedding_model_license: str = DEFAULT_EMBEDDING_MODEL_LICENSE,
    embedding_dim: int = DEFAULT_EMBEDDING_DIM,
    reranker_model_id: str = DEFAULT_RERANKER_MODEL_ID,
    reranker_model_revision: str = DEFAULT_RERANKER_MODEL_REVISION,
    reranker_model_license: str = DEFAULT_RERANKER_MODEL_LICENSE,
    ingest_strategy: str = "rust_md_v1",
    chunk_target_min_tokens: int = 150,
    chunk_target_max_tokens: int = 500,
    chunk_overlap_percent: float = 0.0,
    allow_provenance_mismatch: bool = False,
) -> dict[str, Any]:
    report_root = report_root or (db_path.parents[1] / "reports" / "rust_reference")
    reference_cache_dir = reference_cache_dir or (db_path.parents[1] / "sources" / "rust-reference")
    if retrieval_corpus not in RETRIEVAL_CORPUS_VALUES:
        raise ValueError(
            "Unsupported retrieval corpus "
            f"'{retrieval_corpus}'; expected one of {sorted(RETRIEVAL_CORPUS_VALUES)}"
        )
    if float(chunk_overlap_percent) < 0.0 or float(chunk_overlap_percent) > 0.45:
        raise ValueError(
            f"chunk_overlap_percent must be within [0.0, 0.45]; got {chunk_overlap_percent}"
        )
    if not str(reference_revision or "").strip():
        raise ValueError("Pinned source revision is required; pass --reference-revision explicitly")

    strategy = resolve_ingest_strategy(ingest_strategy)

    existing_manifest = _load_manifest(manifest_path)
    previous_snapshot_path = _read_previous_snapshot_path(
        existing_manifest, manifest_path=manifest_path
    )

    source_dir, commit_sha, source_fetched_at = _resolve_reference_checkout(
        reference_source_dir=reference_source_dir,
        reference_cache_dir=reference_cache_dir,
        reference_repo_url=reference_repo_url,
        reference_revision=reference_revision,
        skip_fetch=skip_fetch,
    )

    source_root = source_dir / "src"
    summary_entries = _parse_summary(source_root)
    documents, chapters = _load_source_documents(
        source_root=source_root,
        summary_entries=summary_entries,
        source_fetched_at=source_fetched_at,
        source_commit_sha=commit_sha,
    )

    snapshot_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    snapshot_id = f"rust-reference-{snapshot_stamp}"

    sections, statements = _extract_sections_and_statements(
        snapshot_id=snapshot_id,
        documents=documents,
        cleaner=lambda text: strategy.clean_text(
            CleanInput(raw_text=text, source_type="markdown", context={"corpus": "rust_reference"})
        ).cleaned_text,
    )
    chunk_result = strategy.build_chunks(
        ChunkInput(
            sections=sections,
            target_min_tokens=int(chunk_target_min_tokens),
            target_max_tokens=int(chunk_target_max_tokens),
            overlap_percent=float(chunk_overlap_percent),
        )
    )
    chunks = [
        ChunkRecord(
            chunk_uid=str(row["chunk_uid"]),
            section_id=str(row["section_id"]),
            raw_text=str(row["raw_text"]),
            clean_text=str(row["clean_text"]),
            char_len=int(row["char_len"]),
            token_len=int(row["token_len"]),
            source_sha256=str(row["source_sha256"]),
            source_fetched_at=str(row["source_fetched_at"]),
            source_commit_sha=str(row["source_commit_sha"]),
            order_index=int(row["order_index"]),
        )
        for row in chunk_result.chunks
    ]
    chunk_spans = [
        ChunkSpanRecord(
            chunk_uid=str(row["chunk_uid"]),
            source_anchor=str(row["source_anchor"]),
            start_offset=int(row["start_offset"]),
            end_offset=int(row["end_offset"]),
            span_order=int(row["span_order"]),
        )
        for row in chunk_result.spans
    ]
    mechanisms, mechanism_evidence, evidence_count_by_mechanism, best_anchor_by_mechanism = (
        _extract_mechanisms_and_evidence(
            sections=sections,
            statements=statements,
            source_fetched_at=source_fetched_at,
            source_url=DEFAULT_REFERENCE_SOURCE_URL,
        )
    )
    semantic_models = _build_semantic_models(
        source_fetched_at=source_fetched_at,
        retrieval_mode=retrieval_mode,
        embedding_model_id=embedding_model_id,
        embedding_model_revision=embedding_model_revision,
        embedding_model_license=embedding_model_license,
        embedding_dim=embedding_dim,
        reranker_model_id=reranker_model_id,
        reranker_model_revision=reranker_model_revision,
        reranker_model_license=reranker_model_license,
    )

    table_rows = _resolve_table1_rows(extractor_db=extractor_db, table_node_id=table_node_id)
    semantic_corpus = _build_semantic_corpus(
        table_rows=table_rows,
        mechanisms=mechanisms,
        mechanism_evidence=mechanism_evidence,
        statements=statements,
        source_fetched_at=source_fetched_at,
    )

    row_verdicts, row_mechanisms, row_mechanism_scores, counts = _build_row_queryability(
        table_rows=table_rows,
        mechanisms=mechanisms,
        mechanism_evidence=mechanism_evidence,
        statements=statements,
        evidence_count_by_mechanism=evidence_count_by_mechanism,
        best_anchor_by_mechanism=best_anchor_by_mechanism,
        source_fetched_at=source_fetched_at,
        retrieval_mode=retrieval_mode,
        semantic_profile_version=semantic_profile_version,
    )

    snapshot_sha256 = _compute_snapshot_sha256(
        commit_sha=commit_sha,
        documents=documents,
        sections=sections,
        statements=statements,
        chunks=chunks,
    )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_root.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    connection = sqlite3.connect(db_path)
    latest_migration_id = ""
    try:
        initialize_schema(connection)
        connection.commit()
        connection.close()
        latest_migration_id, _ = apply_pending_migrations(
            db_path, root=Path(__file__).resolve().parents[3]
        )
        connection = sqlite3.connect(db_path)
        _insert_payload(
            connection=connection,
            snapshot_id=snapshot_id,
            commit_sha=commit_sha,
            fetched_at=source_fetched_at,
            snapshot_sha256=snapshot_sha256,
            chapters=chapters,
            documents=documents,
            sections=sections,
            statements=statements,
            chunks=chunks,
            chunk_spans=chunk_spans,
            mechanisms=mechanisms,
            mechanism_evidence=mechanism_evidence,
            table_rows=table_rows,
            row_verdicts=row_verdicts,
            row_mechanisms=row_mechanisms,
            semantic_models=semantic_models,
            semantic_corpus=semantic_corpus,
            row_mechanism_scores=row_mechanism_scores,
            extractor_version=(
                "sqlite-build-rust-reference-v7::"
                f"{strategy.strategy_id}@{strategy.strategy_version}"
            ),
            build_notes=(
                "chunk-first schema and deterministic block parsing via "
                f"{strategy.strategy_id}@{strategy.strategy_version}"
            ),
        )
        connection.commit()
    finally:
        connection.close()

    snapshot_db_path = snapshot_root / f"{snapshot_id}.sqlite"
    shutil.copy2(db_path, snapshot_db_path)

    validation_report = validate_rust_reference_db(
        db_path=db_path,
        previous_snapshot_path=previous_snapshot_path,
        min_sections=min_sections,
        min_statements=min_statements,
        min_mechanisms=min_mechanisms,
    )
    validation_report.update(
        {
            "snapshot_id": snapshot_id,
            "commit_sha": commit_sha,
            "documents": len(documents),
            "chapters": len(chapters),
            "sections": len(sections),
            "statements": len(statements),
            "chunks": len(chunks),
            "mechanisms": len(mechanisms),
            "mechanism_evidence": len(mechanism_evidence),
            "source_fetched_at": source_fetched_at,
        }
    )
    report_path = _write_validation_report(
        report_root=report_root, snapshot_id=snapshot_id, payload=validation_report
    )
    row_metadata_report_path = _write_row_metadata_report(
        report_root=report_root,
        snapshot_id=snapshot_id,
        table_rows=table_rows,
    )
    if not validation_report["passed"]:
        raise RuntimeError(
            f"Validation failed for rust_reference.sqlite: {validation_report['failures']}"
        )

    _update_manifest(
        manifest_path=manifest_path,
        snapshot_id=snapshot_id,
        current_db_path=db_path,
        snapshot_db_path=snapshot_db_path,
        commit_sha=commit_sha,
        source_fetched_at=source_fetched_at,
        source_url=DEFAULT_REFERENCE_SOURCE_URL,
        report_path=report_path,
        row_metadata_report_path=row_metadata_report_path,
        counts=counts,
        chunk_count=len(chunks),
        chunk_overlap_percent=float(chunk_overlap_percent),
        retrieval_mode=retrieval_mode,
        retrieval_corpus=retrieval_corpus,
        semantic_profile_version=semantic_profile_version,
        embedding_model_id=embedding_model_id,
        reranker_model_id=reranker_model_id,
    )

    source_state = compute_source_state_from_db(db_path)
    model_fingerprint = canonical_json_hash(
        {
            "embed_model_id": str(embedding_model_id),
            "reranker_model_id": str(reranker_model_id),
            "embedding_dim": int(embedding_dim),
        }
    )
    pipeline_fingerprint = record_pipeline_run(
        db_path=db_path,
        run_id=f"build::{snapshot_id}",
        corpus="rust_reference",
        source_state=source_state,
        schema_migration_id=latest_migration_id,
        ingest_strategy=strategy.strategy_id,
        ingest_strategy_version=strategy.strategy_version,
        ingest_params={
            "target_min_tokens": int(chunk_target_min_tokens),
            "target_max_tokens": int(chunk_target_max_tokens),
            "overlap_percent": float(chunk_overlap_percent),
        },
        retrieval_profile_id="rust_reference_control",
        eval_policy_id="rust_reference",
        model_fingerprint=model_fingerprint,
        allow_provenance_mismatch=bool(allow_provenance_mismatch),
    )

    return {
        "snapshot_id": snapshot_id,
        "commit_sha": commit_sha,
        "source_fetched_at": source_fetched_at,
        "db_path": str(db_path),
        "snapshot_db_path": str(snapshot_db_path),
        "validation_report": str(report_path),
        "row_metadata_report": str(row_metadata_report_path),
        "documents": len(documents),
        "chapters": len(chapters),
        "sections": len(sections),
        "statements": len(statements),
        "chunks": len(chunks),
        "mechanisms": len(mechanisms),
        "semantic_models": len(semantic_models),
        "semantic_corpus": len(semantic_corpus),
        "row_mechanism_scores": len(row_mechanism_scores),
        "retrieval_mode": retrieval_mode,
        "retrieval_corpus": retrieval_corpus,
        "ingest_strategy": strategy.strategy_id,
        "ingest_strategy_version": strategy.strategy_version,
        "chunk_target_min_tokens": int(chunk_target_min_tokens),
        "chunk_target_max_tokens": int(chunk_target_max_tokens),
        "chunk_overlap_percent": float(chunk_overlap_percent),
        "pipeline_fingerprint": pipeline_fingerprint,
        "semantic_profile_version": semantic_profile_version,
        "rows_total": counts["applicable"] + counts["not_applicable"],
        "applicable": counts["applicable"],
        "not_applicable": counts["not_applicable"],
    }


def parse_args() -> argparse.Namespace:
    return parse_build_args(
        default_extractor_db=DEFAULT_EXTRACTOR_DB,
        default_table_node_id=DEFAULT_TABLE_NODE_ID,
        default_reference_cache_dir=DEFAULT_REFERENCE_CACHE_DIR,
        default_reference_repo_url=DEFAULT_REFERENCE_REPO_URL,
        default_retrieval_mode=DEFAULT_RETRIEVAL_MODE,
        retrieval_corpus_values=RETRIEVAL_CORPUS_VALUES,
        default_retrieval_corpus=DEFAULT_RETRIEVAL_CORPUS,
        default_semantic_profile_version=DEFAULT_SEMANTIC_PROFILE_VERSION,
        default_embedding_model_id=DEFAULT_EMBEDDING_MODEL_ID,
        default_embedding_model_revision=DEFAULT_EMBEDDING_MODEL_REVISION,
        default_embedding_model_license=DEFAULT_EMBEDDING_MODEL_LICENSE,
        default_embedding_dim=DEFAULT_EMBEDDING_DIM,
        default_reranker_model_id=DEFAULT_RERANKER_MODEL_ID,
        default_reranker_model_revision=DEFAULT_RERANKER_MODEL_REVISION,
        default_reranker_model_license=DEFAULT_RERANKER_MODEL_LICENSE,
    )


def run_rust_reference_build(*, args: argparse.Namespace, root: Path) -> dict[str, Any]:
    db_path = (root / args.db_path).resolve()
    snapshot_root = (root / args.snapshot_root).resolve()
    manifest_path = (root / args.manifest_path).resolve()
    report_root = (root / args.report_root).resolve()
    extractor_db = Path(args.extractor_db).expanduser().resolve()
    reference_source_dir = (
        Path(args.reference_source_dir).expanduser().resolve()
        if args.reference_source_dir
        else None
    )
    reference_cache_dir = (root / args.reference_cache_dir).resolve()

    return build_rust_reference_db(
        db_path=db_path,
        snapshot_root=snapshot_root,
        manifest_path=manifest_path,
        extractor_db=extractor_db,
        table_node_id=args.table_node_id,
        reference_source_dir=reference_source_dir,
        reference_cache_dir=reference_cache_dir,
        reference_repo_url=args.reference_repo_url,
        reference_revision=args.reference_revision,
        skip_fetch=args.skip_fetch,
        report_root=report_root,
        min_sections=args.min_sections,
        min_statements=args.min_statements,
        min_mechanisms=args.min_mechanisms,
        retrieval_mode=args.retrieval_mode,
        retrieval_corpus=args.retrieval_corpus,
        semantic_profile_version=args.semantic_profile_version,
        embedding_model_id=args.embedding_model_id,
        embedding_model_revision=args.embedding_model_revision,
        embedding_model_license=args.embedding_model_license,
        embedding_dim=args.embedding_dim,
        reranker_model_id=args.reranker_model_id,
        reranker_model_revision=args.reranker_model_revision,
        reranker_model_license=args.reranker_model_license,
        ingest_strategy=args.ingest_strategy,
        chunk_target_min_tokens=args.chunk_target_min_tokens,
        chunk_target_max_tokens=args.chunk_target_max_tokens,
        chunk_overlap_percent=args.chunk_overlap_percent,
        allow_provenance_mismatch=args.allow_provenance_mismatch,
    )


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[3]

    try:
        from retrieval.builders.registry import resolve_builder

        runner = resolve_builder(str(args.corpus))
        summary = runner(args=args, root=root)
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"[build-rust-reference][error] {exc}")
        return EXIT_RUNTIME_FAIL

    print(json.dumps(summary, indent=2, sort_keys=True))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
