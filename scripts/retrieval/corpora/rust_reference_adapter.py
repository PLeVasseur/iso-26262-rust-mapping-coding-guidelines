from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from retrieval.corpora.base import CorpusAdapterConfig


@dataclass(frozen=True)
class RustReferenceAdapter:
    config: CorpusAdapterConfig = CorpusAdapterConfig(
        corpus_name="rust_reference",
        default_db_path=Path(".cache/sqlite_kb/current/rust_reference.sqlite"),
        default_contract_path=Path("config/sqlite_query_contracts/rust_reference_chunk.yaml"),
    )
