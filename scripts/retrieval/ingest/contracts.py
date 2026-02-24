from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CleanInput:
    raw_text: str
    source_type: str
    context: dict[str, Any]


@dataclass(frozen=True)
class CleanResult:
    cleaned_text: str
    normalizer_version: str


@dataclass(frozen=True)
class ChunkInput:
    sections: list[Any]
    target_min_tokens: int
    target_max_tokens: int


@dataclass(frozen=True)
class ChunkResult:
    chunks: list[dict[str, Any]]
    spans: list[dict[str, Any]]
    strategy_version: str


@dataclass(frozen=True)
class SourceState:
    source_revision: str
    source_fingerprint: str
    source_timestamp: str
    details: dict[str, Any]


class TextCleaner(Protocol):
    def clean_text(self, clean_input: CleanInput) -> CleanResult: ...


class Chunker(Protocol):
    def build_chunks(self, chunk_input: ChunkInput) -> ChunkResult: ...


class IngestStrategy(Protocol):
    @property
    def strategy_id(self) -> str: ...

    @property
    def strategy_version(self) -> str: ...

    def clean_text(self, clean_input: CleanInput) -> CleanResult: ...

    def build_chunks(self, chunk_input: ChunkInput) -> ChunkResult: ...
