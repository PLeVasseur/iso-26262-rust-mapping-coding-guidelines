from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from retrieval.corpora.registry import get_corpus_adapter


@dataclass(frozen=True)
class CorpusRuntimePaths:
    corpus: str
    db_path: Path
    contract_path: Path
    query_log_root: Path
    rewrite_rules_path: Path
    report_root: Path


def _resolve(root: Path, raw: str) -> Path:
    path = Path(str(raw).strip())
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def resolve_corpus_runtime_paths(
    *,
    root: Path,
    corpus: str,
    db_path: str,
    contract_path: str,
    query_log_root: str,
    rewrite_rules_path: str,
) -> CorpusRuntimePaths:
    normalized_corpus = str(corpus).strip().lower()
    corpus_config = get_corpus_adapter(normalized_corpus).config

    db_path_raw = str(db_path).strip() or str(corpus_config.default_db_path)
    contract_path_raw = str(contract_path).strip() or str(corpus_config.default_contract_path)
    query_log_root_raw = (
        str(query_log_root).strip() or f".cache/sqlite_kb/query_logs/{normalized_corpus}"
    )
    rewrite_rules_raw = str(rewrite_rules_path).strip() or (
        f"config/sqlite_query_rewrite/{normalized_corpus}_rewrite.yaml"
    )

    return CorpusRuntimePaths(
        corpus=normalized_corpus,
        db_path=_resolve(root, db_path_raw),
        contract_path=_resolve(root, contract_path_raw),
        query_log_root=_resolve(root, query_log_root_raw),
        rewrite_rules_path=_resolve(root, rewrite_rules_raw),
        report_root=_resolve(root, f".cache/sqlite_kb/reports/{normalized_corpus}"),
    )
