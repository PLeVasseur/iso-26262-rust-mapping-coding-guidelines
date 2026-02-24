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
        default_eval_path=Path("data/query_testsets/core_docs_table1_retrieval_eval.yaml"),
        default_rewrite_rules_path=Path("config/sqlite_query_rewrite/core_docs_rewrite.yaml"),
        default_query_log_root=Path(".cache/sqlite_kb/query_logs/core_docs"),
        default_report_root=Path(".cache/sqlite_kb/reports/core_docs"),
        default_profile_name="core_docs_control",
        default_eval_policy_path=Path("config/eval_policies/core_docs.yaml"),
    )
