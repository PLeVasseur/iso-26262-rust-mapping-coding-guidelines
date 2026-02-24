from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from retrieval.corpora.base import CorpusAdapterConfig


@dataclass(frozen=True)
class CoreDocsAdapter:
    config: CorpusAdapterConfig = CorpusAdapterConfig(
        corpus_name="core_docs",
        default_db_path=Path(".cache/sqlite_kb/current/core_docs.sqlite"),
        default_contract_path=Path("config/sqlite_query_contracts/core_docs.yaml"),
    )
