from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from retrieval.ingest.contracts import ChunkInput, ChunkResult, CleanInput, CleanResult

DEFAULT_REFERENCE_SOURCE_URL = "https://doc.rust-lang.org/reference/"
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(`\[])")
SEMANTIC_TOKEN_RE = re.compile(r"[a-z0-9_]+")
ADMONITION_TAG_RE = re.compile(r"\[![A-Z]+\]")
FOOTNOTE_MARKER_RE = re.compile(r"\[\^[^\]]+\]")
RAW_ARTIFACT_RE = re.compile(r"\br\[[^\]]+\]")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _semantic_tokens(value: str) -> set[str]:
    normalized = _normalize_semantic_text(value).lower()
    return set(SEMANTIC_TOKEN_RE.findall(normalized))


def _normalize_semantic_text(value: str) -> str:
    return " ".join(str(value).split())


def _anchor_url_for_section(section: Any) -> str:
    html_path = Path(str(getattr(section, "rel_path", ""))).with_suffix(".html")
    anchor = str(getattr(section, "anchor", ""))
    return f"{DEFAULT_REFERENCE_SOURCE_URL}{html_path.as_posix()}#{anchor}"


def _clean_chunk_text(raw_text: str) -> str:
    value = CODE_FENCE_RE.sub(" ", str(raw_text))
    value = HTML_COMMENT_RE.sub(" ", value)
    value = ADMONITION_TAG_RE.sub(" ", value)
    value = FOOTNOTE_MARKER_RE.sub(" ", value)
    value = RAW_ARTIFACT_RE.sub(" ", value)
    value = value.replace("`", " ")
    return " ".join(value.split())


def _split_section_blocks(text: str) -> list[str]:
    blocks = [value.strip() for value in str(text).split("\n\n") if value.strip()]
    return blocks or [str(text).strip()]


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


@dataclass(frozen=True)
class RustMarkdownV1Strategy:
    strategy_id: str = "rust_md_v1"
    strategy_version: str = "1"

    def clean_text(self, clean_input: CleanInput) -> CleanResult:
        cleaned = _clean_chunk_text(clean_input.raw_text)
        return CleanResult(cleaned_text=cleaned, normalizer_version="clean-v1")

    def build_chunks(self, chunk_input: ChunkInput) -> ChunkResult:
        target_min = max(40, int(chunk_input.target_min_tokens))
        target_max = max(target_min, int(chunk_input.target_max_tokens))

        chunks: list[dict[str, Any]] = []
        spans: list[dict[str, Any]] = []

        for section in sorted(
            chunk_input.sections,
            key=lambda row: (
                str(getattr(row, "document_id", "")),
                int(getattr(row, "order_index", 0)),
            ),
        ):
            source_anchor = _anchor_url_for_section(section)
            section_text = str(getattr(section, "text", ""))
            section_text_lower = section_text.lower()
            section_blocks = _split_section_blocks(section_text)

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
                    "::".join(
                        (
                            str(getattr(section, "section_id", "")),
                            str(order_index),
                            clean_text.lower(),
                        )
                    )
                )
                chunk_uid = f"chunk::{chunk_fingerprint}"

                chunks.append(
                    {
                        "chunk_uid": chunk_uid,
                        "section_id": str(getattr(section, "section_id", "")),
                        "raw_text": raw_text,
                        "clean_text": clean_text,
                        "char_len": len(clean_text),
                        "token_len": len(_semantic_tokens(clean_text)),
                        "source_sha256": str(getattr(section, "source_sha256", "")),
                        "source_fetched_at": str(getattr(section, "source_fetched_at", "")),
                        "source_commit_sha": str(getattr(section, "source_commit_sha", "")),
                        "order_index": int(order_index),
                    }
                )
                spans.append(
                    {
                        "chunk_uid": chunk_uid,
                        "source_anchor": source_anchor,
                        "start_offset": int(start_offset),
                        "end_offset": int(end_offset),
                        "span_order": 1,
                    }
                )

        return ChunkResult(chunks=chunks, spans=spans, strategy_version=self.strategy_version)
