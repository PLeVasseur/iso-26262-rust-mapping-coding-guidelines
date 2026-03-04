from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

SUMMARY_ENTRY_RE = re.compile(r"^(\s*)[-*]\s+\[([^\]]+)\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
EXPLICIT_ANCHOR_RE = re.compile(r"\s*\{#([A-Za-z0-9_-]+)\}\s*$")
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(`\[])")
ADMONITION_TAG_RE = re.compile(r"\[![A-Z]+\]")
FOOTNOTE_MARKER_RE = re.compile(r"\[\^[^\]]+\]")
RAW_ARTIFACT_RE = re.compile(r"\br\[[^\]]+\]")

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


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).lower())
    normalized = normalized.strip("-")
    return normalized or "untitled"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parse_summary(src_root: Any) -> list[SummaryEntry]:
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
        title = match.group(2).strip()
        rel_path = match.group(3).strip()
        rel_path = re.sub(r"#[^#]+$", "", rel_path)
        if not rel_path.endswith(".md"):
            continue

        if indent == 0:
            chapter_order += 1
            current_chapter_title = title
            current_chapter_id = f"chapter:{chapter_order:03d}:{_slugify(title)}"
            doc_order = 0

        if current_chapter_id is None:
            chapter_order += 1
            current_chapter_title = title
            current_chapter_id = f"chapter:{chapter_order:03d}:{_slugify(title)}"

        if rel_path in seen_paths:
            continue

        doc_order += 1
        chapter_title = current_chapter_title or title
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


def load_source_documents(
    source_root: Any,
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


def clean_chunk_text(raw_text: str) -> str:
    value = CODE_FENCE_RE.sub(" ", str(raw_text))
    value = HTML_COMMENT_RE.sub(" ", value)
    value = ADMONITION_TAG_RE.sub(" ", value)
    value = FOOTNOTE_MARKER_RE.sub(" ", value)
    value = RAW_ARTIFACT_RE.sub(" ", value)
    value = value.replace("`", " ")
    return " ".join(value.split())


def extract_sections_and_statements(
    snapshot_id: str,
    documents: list[SourceDocument],
    cleaner: Callable[[str], str] | None = None,
) -> tuple[list[SectionRecord], list[StatementRecord]]:
    clean_fn = cleaner or clean_chunk_text
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
