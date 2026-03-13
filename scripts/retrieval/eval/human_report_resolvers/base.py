from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChunkColumn:
    key: str
    label: str


@dataclass(frozen=True)
class ChunkRecord:
    chunk_uid: str
    section_heading: str
    source_anchor: str
    snippet: str
    extras: dict[str, str]


class HumanReportResolver(Protocol):
    corpus: str
    extra_columns: tuple[ChunkColumn, ...]

    def fetch_chunk_records(
        self,
        *,
        conn: sqlite3.Connection,
        chunk_ids: list[str],
        snippet_chars: int,
    ) -> dict[str, ChunkRecord]: ...
