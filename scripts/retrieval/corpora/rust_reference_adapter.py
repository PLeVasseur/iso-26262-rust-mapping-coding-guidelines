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
        default_eval_path=Path("data/query_testsets/rust_reference_table1_retrieval_eval.yaml"),
        default_rewrite_rules_path=Path("config/sqlite_query_rewrite/rust_reference_rewrite.yaml"),
        default_query_log_root=Path(".cache/sqlite_kb/query_logs/rust_reference"),
        default_report_root=Path(".cache/sqlite_kb/reports/rust_reference"),
        default_profile_name="rust_reference_control",
        default_eval_policy_path=Path("config/eval_policies/rust_reference.yaml"),
    )
