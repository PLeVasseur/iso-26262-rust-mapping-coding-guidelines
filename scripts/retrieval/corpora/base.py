from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class CorpusAdapterConfig:
    corpus_name: str
    default_db_path: Path
    default_contract_path: Path
    default_eval_path: Path
    default_rewrite_rules_path: Path
    default_query_log_root: Path
    default_report_root: Path
    default_profile_name: str
    default_eval_policy_path: Path
    supports_query: bool = True
    supports_eval: bool = True
    supports_build: bool = True
    supports_materialize: bool = True
    supports_smoke: bool = True
    supports_capture: bool = True
    supports_verify: bool = True
    supports_validate: bool = True
    supports_migrate: bool = True


class CorpusAdapter(Protocol):
    @property
    def config(self) -> CorpusAdapterConfig: ...
