#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3

DEFAULT_EXTRACTOR_DB = Path(
    "/home/pete.levasseur/personal/iso-26262-coding-standard-extraction/"
    ".cache/iso26262/iso26262_index.sqlite"
)
DEFAULT_TABLE_NODE_ID = "ISO26262-6-2018:node:table:table_1:001"
DEFAULT_REFERENCE_REPO_URL = "https://github.com/rust-lang/reference.git"
DEFAULT_REFERENCE_CACHE_DIR = ".cache/sqlite_kb/sources/rust-reference"
DEFAULT_REFERENCE_SOURCE_URL = "https://doc.rust-lang.org/reference/"

SUMMARY_ENTRY_RE = re.compile(r"^(\s*)[-*]\s+\[([^\]]+)\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
EXPLICIT_ANCHOR_RE = re.compile(r"\s*\{#([A-Za-z0-9_-]+)\}\s*$")
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(`\[])")

STATEMENT_TYPE_PATTERNS: list[tuple[str, re.Pattern[str], float]] = [
    (
        "constraint",
        re.compile(
            r"\b(must|shall|required|cannot|must not|only|never|forbidden)\b", re.IGNORECASE
        ),
        0.88,
    ),
    (
        "definition",
        re.compile(r"\b(is|are|means|refers to|called|denotes|defined as)\b", re.IGNORECASE),
        0.76,
    ),
]


@dataclass(frozen=True)
class SummaryEntry:
    rel_path: str
    title: str
    chapter_id: str
    chapter_title: str
    chapter_order: int
    doc_order: int


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    rel_path: str
    title: str
    chapter_id: str
    chapter_title: str
    chapter_order: int
    doc_order: int
    body: str
    source_sha256: str
    source_fetched_at: str
    source_commit_sha: str


@dataclass(frozen=True)
class SectionRecord:
    section_id: str
    snapshot_id: str
    document_id: str
    chapter_id: str
    anchor: str
    heading: str
    order_index: int
    level: int
    text: str
    source_sha256: str
    source_fetched_at: str
    source_commit_sha: str
    rel_path: str


@dataclass(frozen=True)
class StatementRecord:
    statement_id: str
    section_id: str
    statement_type: str
    text: str
    confidence: float
    sentence_index: int
    source_sha256: str
    source_fetched_at: str
    source_commit_sha: str


@dataclass(frozen=True)
class MechanismDefinition:
    mechanism_id: str
    canonical_symbol: str
    mechanism_family: str
    enforcement_kind: str
    stability: str
    patterns: tuple[re.Pattern[str], ...]


MECHANISM_TAXONOMY: tuple[MechanismDefinition, ...] = (
    MechanismDefinition(
        mechanism_id="rust.typing.static",
        canonical_symbol="type-system",
        mechanism_family="typing",
        enforcement_kind="hard",
        stability="stable",
        patterns=(
            re.compile(r"\btype(s|d)?\b", re.IGNORECASE),
            re.compile(r"\bstruct\b", re.IGNORECASE),
            re.compile(r"\benum\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.typing.newtype",
        canonical_symbol="newtype",
        mechanism_family="typing",
        enforcement_kind="hard",
        stability="stable",
        patterns=(
            re.compile(r"\bnewtype\b", re.IGNORECASE),
            re.compile(r"\btuple struct\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.abstraction.traits",
        canonical_symbol="traits",
        mechanism_family="abstraction",
        enforcement_kind="hard",
        stability="stable",
        patterns=(
            re.compile(r"\btrait(s)?\b", re.IGNORECASE),
            re.compile(r"\bimpl\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.control_flow.match",
        canonical_symbol="match",
        mechanism_family="control-flow",
        enforcement_kind="hard",
        stability="stable",
        patterns=(
            re.compile(r"\bmatch\b", re.IGNORECASE),
            re.compile(r"\bpattern\b", re.IGNORECASE),
            re.compile(r"\bexhaustive\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.defensive.result_option",
        canonical_symbol="result-option",
        mechanism_family="defensive",
        enforcement_kind="hard",
        stability="stable",
        patterns=(
            re.compile(r"\bresult\b", re.IGNORECASE),
            re.compile(r"\boption\b", re.IGNORECASE),
            re.compile(r"\berror\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.memory.borrowing",
        canonical_symbol="ownership-borrowing",
        mechanism_family="memory-safety",
        enforcement_kind="hard",
        stability="stable",
        patterns=(
            re.compile(r"\bborrow(ing)?\b", re.IGNORECASE),
            re.compile(r"\blifetime\b", re.IGNORECASE),
            re.compile(r"\bownership\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.concurrency.send_sync",
        canonical_symbol="send-sync",
        mechanism_family="concurrency",
        enforcement_kind="hard",
        stability="stable",
        patterns=(
            re.compile(r"\bsend\b", re.IGNORECASE),
            re.compile(r"\bsync\b", re.IGNORECASE),
            re.compile(r"\bthread\b", re.IGNORECASE),
            re.compile(r"\batomic\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.unsafe.boundary",
        canonical_symbol="unsafe",
        mechanism_family="unsafe",
        enforcement_kind="review",
        stability="stable",
        patterns=(
            re.compile(r"\bunsafe\b", re.IGNORECASE),
            re.compile(r"\bundefined behavior\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.diagnostics.attributes",
        canonical_symbol="diagnostic-attributes",
        mechanism_family="diagnostics",
        enforcement_kind="lint",
        stability="stable",
        patterns=(
            re.compile(r"\blint\b", re.IGNORECASE),
            re.compile(r"\battribute\b", re.IGNORECASE),
        ),
    ),
)


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


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    normalized = normalized.strip("-")
    return normalized or "section"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _run_git_command(command: list[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *command],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
        raise RuntimeError(f"git {' '.join(command)} failed: {message}")
    return completed.stdout.strip()


def _resolve_reference_checkout(
    reference_source_dir: Path | None,
    reference_cache_dir: Path,
    reference_repo_url: str,
    reference_revision: str | None,
    skip_fetch: bool,
) -> tuple[Path, str, str]:
    if reference_source_dir is not None:
        source_dir = reference_source_dir.resolve()
        if not source_dir.exists():
            raise RuntimeError(f"Reference source directory not found: {source_dir}")
        commit_sha = "local-source"
        if (source_dir / ".git").exists():
            if reference_revision:
                commit_sha = _run_git_command(["rev-parse", reference_revision], cwd=source_dir)
                _run_git_command(["checkout", "--quiet", "--detach", commit_sha], cwd=source_dir)
            else:
                commit_sha = _run_git_command(["rev-parse", "HEAD"], cwd=source_dir)
        elif reference_revision:
            commit_sha = reference_revision
        return source_dir, commit_sha, utc_now()

    source_dir = reference_cache_dir.resolve()
    if not (source_dir / ".git").exists():
        source_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_git_command(["clone", "--quiet", "--depth", "1", reference_repo_url, str(source_dir)])

    if not skip_fetch:
        _run_git_command(["fetch", "--quiet", "origin"], cwd=source_dir)

    if reference_revision:
        commit_sha = _run_git_command(["rev-parse", reference_revision], cwd=source_dir)
    else:
        remote_head = _run_git_command(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=source_dir)
        commit_sha = _run_git_command(["rev-parse", remote_head], cwd=source_dir)

    _run_git_command(["checkout", "--quiet", "--detach", commit_sha], cwd=source_dir)
    return source_dir, commit_sha, utc_now()


def _parse_summary(src_root: Path) -> list[SummaryEntry]:
    summary_path = src_root / "SUMMARY.md"
    if not summary_path.exists():
        raise RuntimeError(f"Rust Reference summary missing: {summary_path}")

    lines = summary_path.read_text(encoding="utf-8").splitlines()
    entries: list[SummaryEntry] = []
    seen_paths: set[str] = set()
    chapter_order = 0
    current_chapter_id: str | None = None
    current_chapter_title: str | None = None
    doc_order = 0

    for raw_line in lines:
        match = SUMMARY_ENTRY_RE.match(raw_line)
        if match is None:
            continue

        indent = len(match.group(1).replace("\t", "    "))
        depth = indent // 2
        title = match.group(2).strip()
        rel_target = match.group(3).strip().split("#", 1)[0]
        if not rel_target.endswith(".md"):
            continue

        rel_path = str(Path(rel_target))
        if rel_path in seen_paths:
            continue

        if depth == 0 or current_chapter_id is None or current_chapter_title is None:
            chapter_order += 1
            current_chapter_title = title
            current_chapter_id = f"chapter:{chapter_order:03d}:{_slugify(title)}"

        chapter_title = current_chapter_title if current_chapter_title is not None else title

        doc_order += 1
        entries.append(
            SummaryEntry(
                rel_path=rel_path,
                title=title,
                chapter_id=current_chapter_id,
                chapter_title=chapter_title,
                chapter_order=chapter_order,
                doc_order=doc_order,
            )
        )
        seen_paths.add(rel_path)

    if not entries:
        raise RuntimeError("No markdown entries parsed from Rust Reference SUMMARY.md")
    return entries


def _load_source_documents(
    source_root: Path,
    summary_entries: list[SummaryEntry],
    source_fetched_at: str,
    source_commit_sha: str,
) -> tuple[list[SourceDocument], list[dict[str, Any]]]:
    documents: list[SourceDocument] = []
    chapter_seen: set[str] = set()
    chapters: list[dict[str, Any]] = []

    for entry in summary_entries:
        if entry.chapter_id not in chapter_seen:
            chapters.append(
                {
                    "chapter_id": entry.chapter_id,
                    "title": entry.chapter_title,
                    "order_index": entry.chapter_order,
                }
            )
            chapter_seen.add(entry.chapter_id)

        doc_path = source_root / entry.rel_path
        if not doc_path.exists():
            continue

        body_bytes = doc_path.read_bytes()
        body_text = body_bytes.decode("utf-8", errors="replace")
        document_id = f"doc::{entry.rel_path.replace('/', '::')}"

        documents.append(
            SourceDocument(
                document_id=document_id,
                rel_path=entry.rel_path,
                title=entry.title,
                chapter_id=entry.chapter_id,
                chapter_title=entry.chapter_title,
                chapter_order=entry.chapter_order,
                doc_order=entry.doc_order,
                body=body_text,
                source_sha256=_sha256_bytes(body_bytes),
                source_fetched_at=source_fetched_at,
                source_commit_sha=source_commit_sha,
            )
        )

    if not documents:
        raise RuntimeError("No Rust Reference markdown documents were loaded")

    return documents, chapters


def _extract_heading_segments(document: SourceDocument) -> list[tuple[int, str, int, int]]:
    heading_matches = list(HEADING_RE.finditer(document.body))
    if not heading_matches:
        return [(1, document.title, 0, len(document.body))]

    segments: list[tuple[int, str, int, int]] = []
    for index, match in enumerate(heading_matches):
        level = len(match.group(1))
        raw_title = match.group(2).strip()
        explicit_anchor_match = EXPLICIT_ANCHOR_RE.search(raw_title)
        if explicit_anchor_match is not None:
            raw_title = raw_title[: explicit_anchor_match.start()].strip()

        start = match.end()
        end = (
            heading_matches[index + 1].start()
            if index + 1 < len(heading_matches)
            else len(document.body)
        )
        segments.append((level, raw_title or f"Section {index + 1}", start, end))
    return segments


def _extract_sections_and_statements(
    snapshot_id: str,
    documents: list[SourceDocument],
) -> tuple[list[SectionRecord], list[StatementRecord]]:
    sections: list[SectionRecord] = []
    statements: list[StatementRecord] = []

    for document in documents:
        segments = _extract_heading_segments(document)
        anchor_counts: dict[str, int] = {}

        for section_order, (level, heading, start, end) in enumerate(segments, start=1):
            section_text = document.body[start:end].strip()
            if not section_text:
                section_text = heading

            anchor_root = _slugify(heading)
            anchor_count = anchor_counts.get(anchor_root, 0)
            anchor_counts[anchor_root] = anchor_count + 1
            anchor = anchor_root if anchor_count == 0 else f"{anchor_root}-{anchor_count + 1}"

            section_id = f"{document.document_id}::section:{section_order:04d}"
            section_record = SectionRecord(
                section_id=section_id,
                snapshot_id=snapshot_id,
                document_id=document.document_id,
                chapter_id=document.chapter_id,
                anchor=anchor,
                heading=heading,
                order_index=section_order,
                level=level,
                text=section_text,
                source_sha256=document.source_sha256,
                source_fetched_at=document.source_fetched_at,
                source_commit_sha=document.source_commit_sha,
                rel_path=document.rel_path,
            )
            sections.append(section_record)

            cleaned = CODE_FENCE_RE.sub(" ", section_text)
            cleaned = HTML_COMMENT_RE.sub(" ", cleaned)
            cleaned = " ".join(cleaned.split())
            raw_sentences = [
                value.strip() for value in SENTENCE_SPLIT_RE.split(cleaned) if value.strip()
            ]
            if not raw_sentences:
                raw_sentences = [heading]

            sentence_index = 0
            for sentence in raw_sentences:
                if len(sentence) < 30:
                    continue
                sentence_index += 1
                if sentence_index > 24:
                    break

                statement_type = "behavior"
                confidence = 0.68
                for candidate_type, pattern, candidate_confidence in STATEMENT_TYPE_PATTERNS:
                    if pattern.search(sentence):
                        statement_type = candidate_type
                        confidence = candidate_confidence
                        break

                statements.append(
                    StatementRecord(
                        statement_id=f"{section_id}::statement:{sentence_index:03d}",
                        section_id=section_id,
                        statement_type=statement_type,
                        text=sentence,
                        confidence=confidence,
                        sentence_index=sentence_index,
                        source_sha256=document.source_sha256,
                        source_fetched_at=document.source_fetched_at,
                        source_commit_sha=document.source_commit_sha,
                    )
                )

    if not sections:
        raise RuntimeError("No sections extracted from Rust Reference documents")

    return sections, statements


def _anchor_url_for_section(section: SectionRecord) -> str:
    html_path = Path(section.rel_path).with_suffix(".html")
    return f"{DEFAULT_REFERENCE_SOURCE_URL}{html_path.as_posix()}#{section.anchor}"


def _extract_mechanisms_and_evidence(
    sections: list[SectionRecord],
    statements: list[StatementRecord],
    source_fetched_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], dict[str, dict[str, Any]]]:
    statement_by_section: dict[str, list[StatementRecord]] = {}
    for statement in statements:
        statement_by_section.setdefault(statement.section_id, []).append(statement)

    mechanisms: dict[str, dict[str, Any]] = {}
    evidence_rows: list[dict[str, Any]] = []
    evidence_counter = 0
    evidence_count_by_mechanism: dict[str, int] = {}
    best_anchor_by_mechanism: dict[str, dict[str, Any]] = {}
    seen_evidence_keys: set[tuple[str, str, str | None]] = set()

    for section in sections:
        section_statements = statement_by_section.get(section.section_id, [])
        section_text = f"{section.heading}\n{section.text}"
        lower_section_text = section_text.lower()
        section_anchor = _anchor_url_for_section(section)

        for mechanism in MECHANISM_TAXONOMY:
            if not any(pattern.search(lower_section_text) for pattern in mechanism.patterns):
                continue

            mechanisms.setdefault(
                mechanism.mechanism_id,
                {
                    "mechanism_id": mechanism.mechanism_id,
                    "canonical_symbol": mechanism.canonical_symbol,
                    "mechanism_family": mechanism.mechanism_family,
                    "enforcement_kind": mechanism.enforcement_kind,
                    "stability": mechanism.stability,
                },
            )

            best_statement: StatementRecord | None = None
            for statement in section_statements:
                if any(pattern.search(statement.text.lower()) for pattern in mechanism.patterns):
                    best_statement = statement
                    break

            statement_id = best_statement.statement_id if best_statement is not None else None
            key = (mechanism.mechanism_id, section.section_id, statement_id)
            if key in seen_evidence_keys:
                continue
            seen_evidence_keys.add(key)

            evidence_counter += 1
            excerpt = best_statement.text if best_statement else section.heading
            confidence = best_statement.confidence if best_statement else 0.64
            evidence_rows.append(
                {
                    "evidence_id": f"evidence::{evidence_counter:06d}",
                    "mechanism_id": mechanism.mechanism_id,
                    "section_id": section.section_id,
                    "statement_id": statement_id,
                    "source_anchor": section_anchor,
                    "evidence_kind": "normative",
                    "text_excerpt": excerpt,
                    "confidence": confidence,
                    "source_fetched_at": source_fetched_at,
                }
            )

            evidence_count_by_mechanism[mechanism.mechanism_id] = (
                evidence_count_by_mechanism.get(mechanism.mechanism_id, 0) + 1
            )
            current_best = best_anchor_by_mechanism.get(mechanism.mechanism_id)
            if current_best is None or confidence > current_best["confidence"]:
                best_anchor_by_mechanism[mechanism.mechanism_id] = {
                    "source_anchor": section_anchor,
                    "section_id": section.section_id,
                    "statement_id": statement_id,
                    "confidence": confidence,
                }

    if not mechanisms:
        raise RuntimeError(
            "Mechanism extraction failed: no mechanisms detected from Rust Reference"
        )

    return (
        sorted(mechanisms.values(), key=lambda value: value["mechanism_id"]),
        evidence_rows,
        evidence_count_by_mechanism,
        best_anchor_by_mechanism,
    )


def _normalize_marker(raw: str | None, row_idx: int) -> str:
    if raw:
        match = re.search(r"1[a-i]", raw.lower())
        if match:
            return match.group(0)
    return f"1{chr(ord('a') + row_idx - 1)}"


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
        resolved.append(
            {
                "row_node_id": str(row["row_node_id"]),
                "row_idx": row_idx,
                "row_marker": marker,
                "requirement_text": str(row["requirement_text"] or "").strip(),
            }
        )

    markers = {row["row_marker"] for row in resolved}
    expected = {f"1{chr(ord('a') + idx)}" for idx in range(9)}
    if markers != expected:
        raise RuntimeError(f"Unexpected Table 1 marker set from extractor: {sorted(markers)}")
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


def _build_row_queryability(
    table_rows: list[dict[str, Any]],
    mechanisms: list[dict[str, Any]],
    evidence_count_by_mechanism: dict[str, int],
    best_anchor_by_mechanism: dict[str, dict[str, Any]],
    source_fetched_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    mechanisms_by_family: dict[str, list[dict[str, Any]]] = {}
    for mechanism in mechanisms:
        mechanisms_by_family.setdefault(mechanism["mechanism_family"], []).append(mechanism)

    for _family, family_mechanisms in mechanisms_by_family.items():
        family_mechanisms.sort(
            key=lambda value: (
                -evidence_count_by_mechanism.get(value["mechanism_id"], 0),
                value["mechanism_id"],
            )
        )

    all_mechanisms_sorted = sorted(
        mechanisms,
        key=lambda value: (
            -evidence_count_by_mechanism.get(value["mechanism_id"], 0),
            value["mechanism_id"],
        ),
    )

    row_verdicts: list[dict[str, Any]] = []
    row_mechanisms: list[dict[str, Any]] = []
    applicable = 0
    not_applicable = 0

    for row in sorted(table_rows, key=lambda item: item["row_marker"]):
        families = _resolve_row_families(row["row_marker"], row["requirement_text"])
        selected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for family in families:
            for mechanism in mechanisms_by_family.get(family, []):
                if mechanism["mechanism_id"] in seen_ids:
                    continue
                selected.append(mechanism)
                seen_ids.add(mechanism["mechanism_id"])
                if len(selected) >= 3:
                    break
            if len(selected) >= 3:
                break

        if not selected:
            for mechanism in all_mechanisms_sorted[:2]:
                if mechanism["mechanism_id"] in seen_ids:
                    continue
                selected.append(mechanism)
                seen_ids.add(mechanism["mechanism_id"])

        if selected:
            applicable += 1
            top = selected[0]
            top_anchor = best_anchor_by_mechanism.get(top["mechanism_id"], {})
            rationale_anchor = str(top_anchor.get("source_anchor", DEFAULT_REFERENCE_SOURCE_URL))
            rationale = (
                "Rust Reference sections provide language semantics relevant to "
                f"ISO 26262 Part 6 Table 1 item {row['row_marker']} "
                f"({', '.join(families)})."
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

            max_rank = float(len(selected))
            for index, mechanism in enumerate(selected):
                evidence = best_anchor_by_mechanism.get(mechanism["mechanism_id"], {})
                row_mechanisms.append(
                    {
                        "row_node_id": row["row_node_id"],
                        "mechanism_id": mechanism["mechanism_id"],
                        "relevance_score": max_rank - float(index),
                        "evidence_anchor": str(
                            evidence.get("source_anchor", DEFAULT_REFERENCE_SOURCE_URL)
                        ),
                        "evidence_section_id": str(evidence.get("section_id", "")),
                        "evidence_statement_id": str(evidence.get("statement_id", "")),
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

        CREATE INDEX IF NOT EXISTS idx_chapters_order ON chapters(order_index);
        CREATE INDEX IF NOT EXISTS idx_documents_chapter
            ON source_documents(chapter_id, order_index);
        CREATE INDEX IF NOT EXISTS idx_sections_document ON sections(document_id, order_index);
        CREATE INDEX IF NOT EXISTS idx_sections_chapter ON sections(chapter_id, order_index);
        CREATE INDEX IF NOT EXISTS idx_statements_section ON statements(section_id, sentence_index);
        CREATE INDEX IF NOT EXISTS idx_mechanism_evidence_mech ON mechanism_evidence(mechanism_id);
        CREATE INDEX IF NOT EXISTS idx_table1_rows_marker ON table1_rows(row_marker);
        CREATE INDEX IF NOT EXISTS idx_row_mechanisms_row ON row_mechanisms(row_node_id);

        PRAGMA user_version = 2;
        """
    )


def _compute_snapshot_sha256(
    commit_sha: str,
    documents: list[SourceDocument],
    sections: list[SectionRecord],
    statements: list[StatementRecord],
) -> str:
    payload = {
        "commit_sha": commit_sha,
        "document_hashes": sorted((doc.rel_path, doc.source_sha256) for doc in documents),
        "sections": len(sections),
        "statements": len(statements),
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
    mechanisms: list[dict[str, Any]],
    mechanism_evidence: list[dict[str, Any]],
    table_rows: list[dict[str, Any]],
    row_verdicts: list[dict[str, Any]],
    row_mechanisms: list[dict[str, Any]],
) -> None:
    connection.execute(
        """
        INSERT INTO snapshots(snapshot_id, commit_sha, source_url, fetched_at, sha256)
        VALUES(?, ?, ?, ?, ?)
        """,
        (snapshot_id, commit_sha, DEFAULT_REFERENCE_SOURCE_URL, fetched_at, snapshot_sha256),
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


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "updated_at": utc_now(), "databases": {}}
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        return {"version": 1, "updated_at": utc_now(), "databases": {}}
    payload.setdefault("version", 1)
    payload.setdefault("databases", {})
    return payload


def _read_previous_snapshot_path(manifest_payload: dict[str, Any]) -> Path | None:
    rust_ref = (manifest_payload.get("databases") or {}).get("rust_reference") or {}
    snapshot_path = rust_ref.get("snapshot_path")
    if isinstance(snapshot_path, str) and snapshot_path:
        return Path(snapshot_path)
    return None


def validate_rust_reference_db(
    db_path: Path,
    previous_snapshot_path: Path | None = None,
    min_sections: int = 20,
    min_statements: int = 50,
    min_mechanisms: int = 6,
) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []

    connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row

        section_count = int(connection.execute("SELECT COUNT(*) FROM sections").fetchone()[0])
        statement_count = int(connection.execute("SELECT COUNT(*) FROM statements").fetchone()[0])
        mechanism_count = int(connection.execute("SELECT COUNT(*) FROM mechanisms").fetchone()[0])

        if section_count < int(min_sections):
            failures.append("Too few sections extracted from Rust Reference")
        if statement_count < int(min_statements):
            failures.append("Too few semantic statements extracted from Rust Reference")
        if mechanism_count < int(min_mechanisms):
            failures.append("Too few mechanisms extracted from Rust Reference")

        duplicate_section_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT section_id, COUNT(*) AS c
                    FROM sections
                    GROUP BY section_id
                    HAVING c > 1
                )
                """
            ).fetchone()[0]
        )
        if duplicate_section_count > 0:
            failures.append("Duplicate section_id values detected")

        missing_anchor_count = int(
            connection.execute("SELECT COUNT(*) FROM sections WHERE TRIM(anchor) = ''").fetchone()[
                0
            ]
        )
        if missing_anchor_count > 0:
            failures.append("Sections with missing anchor detected")

        safety_terms = {
            "type": ("type",),
            "unsafe": ("unsafe",),
            "trait": ("trait",),
            "concurrency": ("concurrency", "thread", "send", "sync", "atomic"),
        }
        safety_rows = connection.execute(
            """
            SELECT title AS text_value FROM chapters
            UNION ALL
            SELECT heading AS text_value FROM sections
            """
        ).fetchall()
        safety_text = "\n".join(str(row["text_value"]).lower() for row in safety_rows)
        for required_term, aliases in safety_terms.items():
            if not any(alias in safety_text for alias in aliases):
                failures.append(f"Required high-safety chapter token missing: {required_term}")

        verdict_rows = connection.execute(
            """
            SELECT r.row_marker, rv.verdict, rv.rationale, rv.rationale_anchor
            FROM table1_rows AS r
            JOIN row_verdicts AS rv ON rv.row_node_id = r.row_node_id
            ORDER BY r.row_marker
            """
        ).fetchall()
        if len(verdict_rows) != 9:
            failures.append("Table 1 verdict coverage is incomplete")

        expected_markers = {f"1{chr(ord('a') + idx)}" for idx in range(9)}
        markers = {str(row["row_marker"]) for row in verdict_rows}
        if markers != expected_markers:
            failures.append("Table 1 marker set mismatch")

        for row in verdict_rows:
            verdict = str(row["verdict"])
            if verdict not in {"applicable", "not_applicable"}:
                failures.append(f"Invalid verdict value: {verdict}")
                continue

            if verdict == "not_applicable" and (
                not str(row["rationale"]).strip() or not str(row["rationale_anchor"]).strip()
            ):
                failures.append(
                    f"Missing rationale evidence for not_applicable row {row['row_marker']}"
                )

            if verdict == "applicable":
                count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM row_mechanisms
                        WHERE row_node_id = (
                            SELECT row_node_id
                            FROM table1_rows
                            WHERE row_marker = ?
                        )
                        """,
                        (row["row_marker"],),
                    ).fetchone()[0]
                )
                if count < 1:
                    failures.append(f"Applicable row {row['row_marker']} has no mechanisms")

        if previous_snapshot_path is not None and previous_snapshot_path.exists():
            previous_connection = sqlite3.connect(previous_snapshot_path)
            try:
                previous_section_count = int(
                    previous_connection.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
                )
                previous_statement_count = int(
                    previous_connection.execute("SELECT COUNT(*) FROM statements").fetchone()[0]
                )
            except sqlite3.Error:
                previous_section_count = 0
                previous_statement_count = 0
            finally:
                previous_connection.close()

            if previous_section_count > 0:
                section_delta_ratio = abs(section_count - previous_section_count) / float(
                    previous_section_count
                )
                if section_delta_ratio > 0.35:
                    warnings.append(
                        "Section count drift exceeds 35% compared with previous snapshot "
                        f"({previous_section_count} -> {section_count})"
                    )

            if previous_statement_count > 0:
                statement_delta_ratio = abs(statement_count - previous_statement_count) / float(
                    previous_statement_count
                )
                if statement_delta_ratio > 0.35:
                    warnings.append(
                        "Statement count drift exceeds 35% compared with previous snapshot "
                        f"({previous_statement_count} -> {statement_count})"
                    )
    finally:
        connection.close()

    return {
        "passed": not failures,
        "checked_at": utc_now(),
        "failures": failures,
        "warnings": warnings,
    }


def _write_validation_report(report_root: Path, snapshot_id: str, payload: dict[str, Any]) -> Path:
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / f"{snapshot_id}.json"
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return report_path


def _update_manifest(
    manifest_path: Path,
    snapshot_id: str,
    current_db_path: Path,
    snapshot_db_path: Path,
    commit_sha: str,
    source_fetched_at: str,
    report_path: Path,
    counts: dict[str, int],
) -> None:
    manifest = _load_manifest(manifest_path)
    manifest.setdefault("databases", {})
    manifest["updated_at"] = utc_now()
    manifest["databases"]["rust_reference"] = {
        "db_name": "rust_reference.sqlite",
        "current_path": str(current_db_path),
        "snapshot_id": snapshot_id,
        "snapshot_path": str(snapshot_db_path),
        "source": {
            "kind": "rust-reference",
            "ref": DEFAULT_REFERENCE_SOURCE_URL,
            "commit_sha": commit_sha,
            "fetched_at": source_fetched_at,
        },
        "query_contract": "config/sqlite_query_contracts/rust_reference.yaml",
        "validation_report": str(report_path),
        "table1_queryability": {
            "rows_total": counts["applicable"] + counts["not_applicable"],
            "applicable": counts["applicable"],
            "not_applicable": counts["not_applicable"],
        },
    }

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(manifest, handle, sort_keys=False, allow_unicode=False)


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
) -> dict[str, Any]:
    report_root = report_root or (db_path.parents[1] / "reports" / "rust_reference")
    reference_cache_dir = reference_cache_dir or (db_path.parents[1] / "sources" / "rust-reference")

    existing_manifest = _load_manifest(manifest_path)
    previous_snapshot_path = _read_previous_snapshot_path(existing_manifest)

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
        snapshot_id=snapshot_id, documents=documents
    )
    mechanisms, mechanism_evidence, evidence_count_by_mechanism, best_anchor_by_mechanism = (
        _extract_mechanisms_and_evidence(
            sections=sections,
            statements=statements,
            source_fetched_at=source_fetched_at,
        )
    )
    table_rows = _resolve_table1_rows(extractor_db=extractor_db, table_node_id=table_node_id)
    row_verdicts, row_mechanisms, counts = _build_row_queryability(
        table_rows=table_rows,
        mechanisms=mechanisms,
        evidence_count_by_mechanism=evidence_count_by_mechanism,
        best_anchor_by_mechanism=best_anchor_by_mechanism,
        source_fetched_at=source_fetched_at,
    )

    snapshot_sha256 = _compute_snapshot_sha256(
        commit_sha=commit_sha,
        documents=documents,
        sections=sections,
        statements=statements,
    )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_root.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    connection = sqlite3.connect(db_path)
    try:
        initialize_schema(connection)
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
            mechanisms=mechanisms,
            mechanism_evidence=mechanism_evidence,
            table_rows=table_rows,
            row_verdicts=row_verdicts,
            row_mechanisms=row_mechanisms,
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
            "mechanisms": len(mechanisms),
            "mechanism_evidence": len(mechanism_evidence),
            "source_fetched_at": source_fetched_at,
        }
    )
    report_path = _write_validation_report(
        report_root=report_root, snapshot_id=snapshot_id, payload=validation_report
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
        report_path=report_path,
        counts=counts,
    )

    return {
        "snapshot_id": snapshot_id,
        "commit_sha": commit_sha,
        "source_fetched_at": source_fetched_at,
        "db_path": str(db_path),
        "snapshot_db_path": str(snapshot_db_path),
        "validation_report": str(report_path),
        "documents": len(documents),
        "chapters": len(chapters),
        "sections": len(sections),
        "statements": len(statements),
        "mechanisms": len(mechanisms),
        "rows_total": counts["applicable"] + counts["not_applicable"],
        "applicable": counts["applicable"],
        "not_applicable": counts["not_applicable"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build rust_reference.sqlite (Rank 1)")
    parser.add_argument(
        "--db-path",
        default=".cache/sqlite_kb/current/rust_reference.sqlite",
        help="Target path for active rust_reference.sqlite",
    )
    parser.add_argument(
        "--snapshot-root",
        default=".cache/sqlite_kb/snapshots/rust_reference",
        help="Directory where immutable snapshots are copied",
    )
    parser.add_argument(
        "--manifest-path",
        default="data/sqlite_kb_manifest.yaml",
        help="Manifest path tracking current snapshot metadata",
    )
    parser.add_argument(
        "--report-root",
        default=".cache/sqlite_kb/reports/rust_reference",
        help="Directory for validation report artifacts",
    )
    parser.add_argument(
        "--extractor-db",
        default=str(DEFAULT_EXTRACTOR_DB),
        help="Path to ISO 26262 extractor index sqlite",
    )
    parser.add_argument(
        "--table-node-id",
        default=DEFAULT_TABLE_NODE_ID,
        help="Canonical table node id for ISO 26262 Part 6 Table 1",
    )
    parser.add_argument(
        "--reference-source-dir",
        default=None,
        help="Optional local rust reference source directory (expects src/SUMMARY.md)",
    )
    parser.add_argument(
        "--reference-cache-dir",
        default=DEFAULT_REFERENCE_CACHE_DIR,
        help="Cache path for cloned rust-lang/reference repository",
    )
    parser.add_argument(
        "--reference-repo-url",
        default=DEFAULT_REFERENCE_REPO_URL,
        help="Git URL for Rust Reference repository",
    )
    parser.add_argument(
        "--reference-revision",
        default=None,
        help="Pinned revision/commit/tag for rust reference checkout",
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip git fetch before resolving revision",
    )
    parser.add_argument(
        "--min-sections",
        type=int,
        default=20,
        help="Minimum extracted section count required by validation",
    )
    parser.add_argument(
        "--min-statements",
        type=int,
        default=50,
        help="Minimum extracted statement count required by validation",
    )
    parser.add_argument(
        "--min-mechanisms",
        type=int,
        default=6,
        help="Minimum extracted mechanism count required by validation",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]

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

    try:
        summary = build_rust_reference_db(
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
        )
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"[build-rust-reference][error] {exc}")
        return EXIT_RUNTIME_FAIL

    print(json.dumps(summary, indent=2, sort_keys=True))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
