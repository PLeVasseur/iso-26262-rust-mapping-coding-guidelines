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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from retrieval.core.provenance import (
    apply_pending_migrations,
    compute_source_state_from_db,
    record_pipeline_run,
)
from retrieval.corpora.registry import list_supported_corpora
from retrieval.ingest.contracts import ChunkInput, CleanInput
from retrieval.ingest.registry import list_ingest_strategies, resolve_ingest_strategy

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

SUMMARY_ENTRY_RE = re.compile(r"^(\s*)[-*]\s+\[([^\]]+)\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
EXPLICIT_ANCHOR_RE = re.compile(r"\s*\{#([A-Za-z0-9_-]+)\}\s*$")
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(`\[])")
SEMANTIC_TOKEN_RE = re.compile(r"[a-z0-9_]+")
ADMONITION_TAG_RE = re.compile(r"\[![A-Z]+\]")
FOOTNOTE_MARKER_RE = re.compile(r"\[\^[^\]]+\]")
RAW_ARTIFACT_RE = re.compile(r"\br\[[^\]]+\]")

CLEAN_TEXT_NORMALIZER_VERSION = "clean-v1"

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
    pinned_revision = str(reference_revision or "").strip()
    if not pinned_revision:
        raise RuntimeError(
            "Pinned source revision is required; pass --reference-revision explicitly"
        )

    if reference_source_dir is not None:
        source_dir = reference_source_dir.resolve()
        if not source_dir.exists():
            raise RuntimeError(f"Reference source directory not found: {source_dir}")
        commit_sha = pinned_revision
        if (source_dir / ".git").exists():
            commit_sha = _run_git_command(["rev-parse", pinned_revision], cwd=source_dir)
            _run_git_command(["checkout", "--quiet", "--detach", commit_sha], cwd=source_dir)
        return source_dir, commit_sha, utc_now()

    source_dir = reference_cache_dir.resolve()
    if not (source_dir / ".git").exists():
        source_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_git_command(["clone", "--quiet", "--depth", "1", reference_repo_url, str(source_dir)])

    if not skip_fetch:
        _run_git_command(["fetch", "--quiet", "origin"], cwd=source_dir)

    commit_sha = _run_git_command(["rev-parse", pinned_revision], cwd=source_dir)

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


def _split_section_blocks(section_text: str) -> list[str]:
    lines = str(section_text).splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    in_code_fence = False

    def _flush() -> None:
        nonlocal current
        if not current:
            return
        block = "\n".join(current).strip()
        if block:
            blocks.append(current)
        current = []

    for raw_line in lines:
        line = str(raw_line).rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code_fence:
                current.append(line)
                _flush()
                in_code_fence = False
                continue

            _flush()
            in_code_fence = True
            current.append(line)
            continue

        if in_code_fence:
            current.append(line)
            continue

        if not stripped:
            _flush()
            continue

        current.append(line)

    _flush()
    return ["\n".join(block).strip() for block in blocks if "\n".join(block).strip()]


def _extract_sections_and_statements(
    snapshot_id: str,
    documents: list[SourceDocument],
    cleaner: Callable[[str], str] | None = None,
) -> tuple[list[SectionRecord], list[StatementRecord]]:
    clean_fn = cleaner or _clean_chunk_text
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

            blocks = _split_section_blocks(section_text)
            if not blocks:
                blocks = [heading]

            sentence_index = 0
            for block in blocks:
                cleaned_block = clean_fn(block)
                if not cleaned_block:
                    continue

                raw_sentences = [
                    value.strip()
                    for value in SENTENCE_SPLIT_RE.split(cleaned_block)
                    if value.strip()
                ]
                if not raw_sentences:
                    raw_sentences = [cleaned_block]

                for sentence in raw_sentences:
                    if len(sentence) < 30:
                        continue
                    sentence_index += 1

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


def _clean_chunk_text(raw_text: str) -> str:
    value = CODE_FENCE_RE.sub(" ", str(raw_text))
    value = HTML_COMMENT_RE.sub(" ", value)
    value = ADMONITION_TAG_RE.sub(" ", value)
    value = FOOTNOTE_MARKER_RE.sub(" ", value)
    value = RAW_ARTIFACT_RE.sub(" ", value)
    value = value.replace("`", " ")
    return " ".join(value.split())


def _split_oversized_chunk(text: str, max_tokens: int) -> list[str]:
    cleaned = _clean_chunk_text(text)
    if not cleaned:
        return []

    sentences = [value.strip() for value in SENTENCE_SPLIT_RE.split(cleaned) if value.strip()]
    if not sentences:
        return [cleaned]

    chunks: list[str] = []
    current: list[str] = []
    token_count = 0
    for sentence in sentences:
        sentence_tokens = len(_semantic_tokens(sentence))
        if current and token_count + sentence_tokens > max_tokens:
            chunks.append(" ".join(current).strip())
            current = [sentence]
            token_count = sentence_tokens
            continue
        current.append(sentence)
        token_count += sentence_tokens
    if current:
        chunks.append(" ".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def _build_concept_chunks_from_sections(
    *,
    sections: list[SectionRecord],
    target_min_tokens: int = 150,
    target_max_tokens: int = 500,
) -> tuple[list[ChunkRecord], list[ChunkSpanRecord]]:
    chunks: list[ChunkRecord] = []
    spans: list[ChunkSpanRecord] = []

    target_min = max(40, int(target_min_tokens))
    target_max = max(target_min, int(target_max_tokens))

    for section in sorted(sections, key=lambda row: (row.document_id, row.order_index)):
        source_anchor = _anchor_url_for_section(section)
        section_text = str(section.text)
        section_text_lower = section_text.lower()
        section_blocks = _split_section_blocks(section_text) or [section_text]

        offset_hint = 0
        block_payloads: list[dict[str, Any]] = []
        for raw_block in section_blocks:
            cleaned = _clean_chunk_text(raw_block)
            if not cleaned:
                continue
            token_len = len(_semantic_tokens(cleaned))
            if token_len <= 0:
                continue
            block_lower = raw_block.lower()
            start_offset = section_text_lower.find(block_lower, offset_hint)
            if start_offset < 0 and offset_hint > 0:
                start_offset = section_text_lower.find(block_lower)
            if start_offset < 0:
                start_offset = max(0, min(offset_hint, len(section_text)))
            end_offset = min(len(section_text), start_offset + len(raw_block))
            offset_hint = max(start_offset, end_offset)
            block_payloads.append(
                {
                    "raw": raw_block.strip(),
                    "clean": cleaned,
                    "tokens": token_len,
                    "start": int(start_offset),
                    "end": int(end_offset),
                }
            )

        if not block_payloads:
            continue

        staged_chunks: list[dict[str, Any]] = []
        current_raw: list[str] = []
        current_clean: list[str] = []
        current_tokens = 0
        current_start = int(block_payloads[0]["start"])
        current_end = int(block_payloads[0]["end"])

        for block in block_payloads:
            block_tokens = int(block["tokens"])
            if current_clean and (current_tokens + block_tokens) > target_max:
                staged_chunks.append(
                    {
                        "raw": "\n\n".join(current_raw).strip(),
                        "clean": " ".join(current_clean).strip(),
                        "tokens": int(current_tokens),
                        "start": int(current_start),
                        "end": int(current_end),
                    }
                )
                current_raw = []
                current_clean = []
                current_tokens = 0
                current_start = int(block["start"])
                current_end = int(block["end"])

            if not current_clean:
                current_start = int(block["start"])
            current_end = int(block["end"])
            current_raw.append(str(block["raw"]))
            current_clean.append(str(block["clean"]))
            current_tokens += block_tokens

            if current_tokens >= target_min:
                staged_chunks.append(
                    {
                        "raw": "\n\n".join(current_raw).strip(),
                        "clean": " ".join(current_clean).strip(),
                        "tokens": int(current_tokens),
                        "start": int(current_start),
                        "end": int(current_end),
                    }
                )
                current_raw = []
                current_clean = []
                current_tokens = 0

        if current_clean:
            staged_chunks.append(
                {
                    "raw": "\n\n".join(current_raw).strip(),
                    "clean": " ".join(current_clean).strip(),
                    "tokens": int(current_tokens),
                    "start": int(current_start),
                    "end": int(current_end),
                }
            )

        exploded_chunks: list[dict[str, Any]] = []
        for staged in staged_chunks:
            token_len = int(staged["tokens"])
            if token_len <= target_max:
                exploded_chunks.append(staged)
                continue

            split_clean_chunks = _split_oversized_chunk(str(staged["clean"]), target_max)
            if not split_clean_chunks:
                continue
            for split_clean in split_clean_chunks:
                exploded_chunks.append(
                    {
                        "raw": split_clean,
                        "clean": split_clean,
                        "tokens": len(_semantic_tokens(split_clean)),
                        "start": int(staged["start"]),
                        "end": int(staged["end"]),
                    }
                )

        for order_index, chunk_payload in enumerate(exploded_chunks, start=1):
            clean_text = str(chunk_payload["clean"]).strip()
            raw_text = str(chunk_payload["raw"]).strip()
            if not clean_text:
                continue

            start_offset = int(chunk_payload["start"])
            end_offset = int(chunk_payload["end"])
            chunk_fingerprint = _sha256_text(
                "|".join(
                    [
                        str(section.document_id),
                        str(source_anchor),
                        str(start_offset),
                        str(end_offset),
                        clean_text.lower(),
                    ]
                )
            )
            chunk_uid = f"chunk::{chunk_fingerprint}"

            chunks.append(
                ChunkRecord(
                    chunk_uid=chunk_uid,
                    section_id=section.section_id,
                    raw_text=raw_text,
                    clean_text=clean_text,
                    char_len=len(raw_text),
                    token_len=len(_semantic_tokens(clean_text)),
                    source_sha256=section.source_sha256,
                    source_fetched_at=section.source_fetched_at,
                    source_commit_sha=section.source_commit_sha,
                    order_index=int(order_index),
                )
            )
            spans.append(
                ChunkSpanRecord(
                    chunk_uid=chunk_uid,
                    source_anchor=source_anchor,
                    start_offset=start_offset,
                    end_offset=end_offset,
                    span_order=1,
                )
            )

    return chunks, spans


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


def _read_previous_snapshot_path(
    manifest_payload: dict[str, Any],
    *,
    manifest_path: Path,
) -> Path | None:
    rust_ref = (manifest_payload.get("databases") or {}).get("rust_reference") or {}
    snapshot_path = rust_ref.get("snapshot_path")
    if isinstance(snapshot_path, str) and snapshot_path:
        candidate = Path(snapshot_path)
        if candidate.is_absolute():
            return candidate
        return (manifest_path.parent / candidate).resolve()
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
        semantic_model_count = int(
            connection.execute("SELECT COUNT(*) FROM semantic_models").fetchone()[0]
        )
        semantic_corpus_count = int(
            connection.execute("SELECT COUNT(*) FROM semantic_corpus").fetchone()[0]
        )
        statement_embedding_count = int(
            connection.execute("SELECT COUNT(*) FROM statement_embeddings").fetchone()[0]
        )
        statements_fts_count = int(
            connection.execute("SELECT COUNT(*) FROM statements_fts").fetchone()[0]
        )
        row_mechanism_score_count = int(
            connection.execute("SELECT COUNT(*) FROM row_mechanism_scores").fetchone()[0]
        )

        if section_count < int(min_sections):
            failures.append("Too few sections extracted from Rust Reference")
        if statement_count < int(min_statements):
            failures.append("Too few semantic statements extracted from Rust Reference")
        if mechanism_count < int(min_mechanisms):
            failures.append("Too few mechanisms extracted from Rust Reference")
        if semantic_model_count < 2:
            failures.append("Semantic model metadata is incomplete")
        if semantic_corpus_count < int(mechanism_count + 9):
            failures.append("Semantic corpus coverage is below minimum row/mechanism threshold")
        if statements_fts_count != statement_count:
            failures.append("FTS statement index coverage does not match statements table")
        if row_mechanism_score_count < 9:
            failures.append("Row mechanism score coverage is incomplete")
        if statement_embedding_count == 0:
            warnings.append(
                "No statement embeddings materialized yet; "
                "semantic retrieval will compute on demand"
            )

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


def _write_row_metadata_report(
    report_root: Path,
    snapshot_id: str,
    table_rows: list[dict[str, Any]],
) -> Path:
    report_root.mkdir(parents=True, exist_ok=True)
    rows = sorted(table_rows, key=lambda row: str(row.get("row_marker", "")))
    payload = {
        "snapshot_id": snapshot_id,
        "generated_at": utc_now(),
        "rows": [
            {
                "row_marker": str(row.get("row_marker", "")),
                "row_node_id": str(row.get("row_node_id", "")),
                "requirement_text_len": len(str(row.get("requirement_text", ""))),
                "profile_term_count": len(list(row.get("row_profile_terms", []))),
                "footnote_count": len(list(row.get("row_footnotes", []))),
            }
            for row in rows
        ],
    }
    report_path = report_root / f"{snapshot_id}_table1_row_metadata.json"
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
    row_metadata_report_path: Path,
    counts: dict[str, int],
    chunk_count: int,
    retrieval_mode: str,
    retrieval_corpus: str,
    semantic_profile_version: str,
    embedding_model_id: str,
    reranker_model_id: str,
) -> None:
    base_dir = manifest_path.resolve().parent

    def _repo_relative(path: Path) -> str:
        resolved = path.resolve()
        try:
            return str(resolved.relative_to(base_dir))
        except ValueError:
            return str(resolved)

    manifest = _load_manifest(manifest_path)
    manifest.setdefault("databases", {})
    manifest["updated_at"] = utc_now()
    query_contract_path = (
        "config/sqlite_query_contracts/rust_reference_chunk.yaml"
        if retrieval_corpus == "chunk"
        else "config/sqlite_query_contracts/rust_reference.yaml"
    )
    manifest["databases"]["rust_reference"] = {
        "db_name": "rust_reference.sqlite",
        "current_path": _repo_relative(current_db_path),
        "snapshot_id": snapshot_id,
        "snapshot_path": _repo_relative(snapshot_db_path),
        "source": {
            "kind": "rust-reference",
            "ref": DEFAULT_REFERENCE_SOURCE_URL,
            "commit_sha": commit_sha,
            "fetched_at": source_fetched_at,
        },
        "query_contract": query_contract_path,
        "validation_report": _repo_relative(report_path),
        "row_metadata_report": _repo_relative(row_metadata_report_path),
        "semantic_retrieval": {
            "retrieval_mode": retrieval_mode,
            "retrieval_corpus": retrieval_corpus,
            "profile_version": semantic_profile_version,
            "embedding_model_id": embedding_model_id,
            "reranker_model_id": reranker_model_id,
        },
        "table1_queryability": {
            "rows_total": counts["applicable"] + counts["not_applicable"],
            "applicable": counts["applicable"],
            "not_applicable": counts["not_applicable"],
        },
        "chunk_stats": {
            "chunk_count": int(chunk_count),
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
    allow_provenance_mismatch: bool = False,
) -> dict[str, Any]:
    report_root = report_root or (db_path.parents[1] / "reports" / "rust_reference")
    reference_cache_dir = reference_cache_dir or (db_path.parents[1] / "sources" / "rust-reference")
    if retrieval_corpus not in RETRIEVAL_CORPUS_VALUES:
        raise ValueError(
            "Unsupported retrieval corpus "
            f"'{retrieval_corpus}'; expected one of {sorted(RETRIEVAL_CORPUS_VALUES)}"
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
        report_path=report_path,
        row_metadata_report_path=row_metadata_report_path,
        counts=counts,
        chunk_count=len(chunks),
        retrieval_mode=retrieval_mode,
        retrieval_corpus=retrieval_corpus,
        semantic_profile_version=semantic_profile_version,
        embedding_model_id=embedding_model_id,
        reranker_model_id=reranker_model_id,
    )

    source_state = compute_source_state_from_db(db_path)
    model_fingerprint = _sha256_text(
        "::".join((str(embedding_model_id), str(reranker_model_id), str(embedding_dim)))
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
        "pipeline_fingerprint": pipeline_fingerprint,
        "semantic_profile_version": semantic_profile_version,
        "rows_total": counts["applicable"] + counts["not_applicable"],
        "applicable": counts["applicable"],
        "not_applicable": counts["not_applicable"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build rust_reference.sqlite (Rank 1)")
    parser.add_argument(
        "--corpus",
        choices=list_supported_corpora(),
        default="rust_reference",
        help="Corpus adapter used to resolve build runner",
    )
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
        help="Pinned revision/commit/tag for rust reference checkout (required)",
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
    parser.add_argument(
        "--retrieval-mode",
        choices=("hybrid", "lexical"),
        default=DEFAULT_RETRIEVAL_MODE,
        help="Row-mechanism ranking mode",
    )
    parser.add_argument(
        "--retrieval-corpus",
        choices=RETRIEVAL_CORPUS_VALUES,
        default=DEFAULT_RETRIEVAL_CORPUS,
        help="Retrieval corpus lane to materialize (statement or chunk)",
    )
    parser.add_argument(
        "--semantic-profile-version",
        default=DEFAULT_SEMANTIC_PROFILE_VERSION,
        help="Semantic score profile/version label",
    )
    parser.add_argument(
        "--embedding-model-id",
        default=DEFAULT_EMBEDDING_MODEL_ID,
        help="Embedding model identifier used for semantic retrieval metadata",
    )
    parser.add_argument(
        "--embedding-model-revision",
        default=DEFAULT_EMBEDDING_MODEL_REVISION,
        help="Embedding model revision metadata",
    )
    parser.add_argument(
        "--embedding-model-license",
        default=DEFAULT_EMBEDDING_MODEL_LICENSE,
        help="Embedding model license metadata",
    )
    parser.add_argument(
        "--embedding-dim",
        type=int,
        default=DEFAULT_EMBEDDING_DIM,
        help="Embedding vector dimension metadata",
    )
    parser.add_argument(
        "--reranker-model-id",
        default=DEFAULT_RERANKER_MODEL_ID,
        help="Reranker model identifier used for semantic retrieval metadata",
    )
    parser.add_argument(
        "--reranker-model-revision",
        default=DEFAULT_RERANKER_MODEL_REVISION,
        help="Reranker model revision metadata",
    )
    parser.add_argument(
        "--reranker-model-license",
        default=DEFAULT_RERANKER_MODEL_LICENSE,
        help="Reranker model license metadata",
    )
    parser.add_argument(
        "--ingest-strategy",
        choices=list_ingest_strategies(),
        default="rust_md_v1",
        help="Ingest strategy id for source cleaning/chunking",
    )
    parser.add_argument(
        "--chunk-target-min-tokens",
        type=int,
        default=150,
        help="Minimum target tokens per generated chunk",
    )
    parser.add_argument(
        "--chunk-target-max-tokens",
        type=int,
        default=500,
        help="Maximum target tokens per generated chunk",
    )
    parser.add_argument(
        "--allow-provenance-mismatch",
        action="store_true",
        help="Record build run even if provenance mismatch override is active",
    )
    return parser.parse_args()


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
