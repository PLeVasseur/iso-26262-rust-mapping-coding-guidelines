from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from retrieval.core.provenance import compute_source_state_from_db
from retrieval.corpora.base import CorpusAdapterConfig


@dataclass(frozen=True)
class FlsSpecAdapter:
    config: CorpusAdapterConfig = CorpusAdapterConfig(
        corpus_name="fls_spec",
        default_db_path=Path(".cache/sqlite_kb/current/fls_spec.db"),
        default_contract_path=Path("config/sqlite_query_contracts/fls_spec.yaml"),
        default_eval_path=Path("data/query_testsets/rust_reference_table1_retrieval_eval.yaml"),
        default_rewrite_rules_path=Path("config/sqlite_query_rewrite/fls_spec_rewrite.yaml"),
        default_query_log_root=Path(".cache/sqlite_kb/query_logs/fls_spec"),
        default_report_root=Path(".cache/sqlite_kb/reports/fls_spec"),
        default_profile_name="fls_spec_control",
        default_eval_policy_path=Path("config/eval_policies/rust_reference.yaml"),
    )

    def compute_source_state(self, db_path: Path) -> dict[str, object]:
        return compute_source_state_from_db(db_path)
