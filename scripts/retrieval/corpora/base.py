from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class CorpusAdapterConfig:
    corpus_name: str
    default_db_path: Path
    default_contract_path: Path


class CorpusAdapter(Protocol):
    @property
    def config(self) -> CorpusAdapterConfig: ...
