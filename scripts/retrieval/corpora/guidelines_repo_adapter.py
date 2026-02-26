from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from retrieval.core.provenance import compute_source_state_from_db
from retrieval.corpora.base import CorpusAdapterConfig


@dataclass(frozen=True)
class GuidelinesRepoAdapter:
    config: CorpusAdapterConfig = CorpusAdapterConfig(
        corpus_name="guidelines_repo",
        default_db_path=Path(".cache/sqlite_kb/current/guidelines_repo.sqlite"),
        default_contract_path=Path("config/sqlite_query_contracts/guidelines_repo.yaml"),
        default_eval_path=Path("data/query_testsets/guidelines_repo_placeholder.yaml"),
        default_rewrite_rules_path=Path("config/sqlite_query_rewrite/guidelines_repo_rewrite.yaml"),
        default_query_log_root=Path(".cache/sqlite_kb/query_logs/guidelines_repo"),
        default_report_root=Path(".cache/sqlite_kb/reports/guidelines_repo"),
        default_profile_name="guidelines_repo_control",
        default_eval_policy_path=Path("config/eval_policies/guidelines_repo.yaml"),
        supports_query=False,
        supports_eval=False,
        supports_materialize=False,
        supports_smoke=False,
        supports_capture=False,
        supports_verify=False,
        supports_validate=False,
        supports_inspect=True,
    )

    def compute_source_state(self, db_path: Path) -> dict[str, object]:
        return compute_source_state_from_db(db_path)
