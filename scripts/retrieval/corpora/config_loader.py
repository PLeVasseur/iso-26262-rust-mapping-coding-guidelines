from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from retrieval.corpora.registry import get_corpus_adapter


@dataclass(frozen=True)
class CorpusRuntimeDefaults:
    corpus: str
    db_path: Path
    contract_path: Path
    eval_path: Path
    rewrite_rules_path: Path
    query_log_root: Path
    report_root: Path
    profile_name: str
    eval_policy_path: Path
    ingest_strategy: str
    chunk_target_min_tokens: int
    chunk_target_max_tokens: int
    chunk_overlap_percent: float
    supports_query: bool
    supports_eval: bool
    supports_build: bool
    supports_materialize: bool
    supports_smoke: bool
    supports_capture: bool
    supports_verify: bool
    supports_validate: bool
    supports_migrate: bool
    supports_inspect: bool


def _resolve(root: Path, raw: str | Path) -> Path:
    path = Path(str(raw).strip())
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"Corpus config must be a mapping: {path}")
    return payload


def load_corpus_runtime_defaults(*, root: Path, corpus: str) -> CorpusRuntimeDefaults:
    normalized = str(corpus).strip().lower()
    adapter = get_corpus_adapter(normalized)
    adapter_cfg = adapter.config

    corpus_cfg_path = root / "config" / "corpora" / f"{normalized}.yaml"
    payload = _load_yaml(corpus_cfg_path) if corpus_cfg_path.exists() else {}

    paths = payload.get("paths") or {}
    defaults = payload.get("defaults") or {}
    ingest = payload.get("ingest") or {}
    ingest_chunking = ingest.get("chunking") or {}
    capabilities = payload.get("capabilities") or {}

    db_path = _resolve(root, paths.get("db", adapter_cfg.default_db_path))
    contract_path = _resolve(root, paths.get("contract", adapter_cfg.default_contract_path))
    eval_path = _resolve(root, paths.get("eval_testset", adapter_cfg.default_eval_path))
    rewrite_rules_path = _resolve(
        root, paths.get("rewrite_rules", adapter_cfg.default_rewrite_rules_path)
    )
    query_log_root = _resolve(root, paths.get("query_log_root", adapter_cfg.default_query_log_root))
    report_root = _resolve(root, paths.get("report_root", adapter_cfg.default_report_root))
    eval_policy_path = _resolve(
        root, paths.get("eval_policy", adapter_cfg.default_eval_policy_path)
    )
    min_tokens = int(ingest_chunking.get("target_min_tokens", 150))
    max_tokens = int(ingest_chunking.get("target_max_tokens", 500))
    overlap_percent = float(ingest_chunking.get("overlap_percent", 0.0))
    if min_tokens <= 0 or max_tokens <= 0 or max_tokens < min_tokens:
        raise RuntimeError(
            "Invalid ingest chunking thresholds for corpus "
            f"{normalized}: min={min_tokens}, max={max_tokens}"
        )
    if overlap_percent < 0.0 or overlap_percent >= 1.0:
        raise RuntimeError(
            "Invalid ingest chunk overlap_percent for corpus "
            f"{normalized}: overlap_percent={overlap_percent}"
        )

    return CorpusRuntimeDefaults(
        corpus=normalized,
        db_path=db_path,
        contract_path=contract_path,
        eval_path=eval_path,
        rewrite_rules_path=rewrite_rules_path,
        query_log_root=query_log_root,
        report_root=report_root,
        profile_name=str(defaults.get("profile", adapter_cfg.default_profile_name)).strip(),
        eval_policy_path=eval_policy_path,
        ingest_strategy=str(ingest.get("strategy", "rust_md_v1")).strip() or "rust_md_v1",
        chunk_target_min_tokens=min_tokens,
        chunk_target_max_tokens=max_tokens,
        chunk_overlap_percent=overlap_percent,
        supports_query=bool(capabilities.get("query", adapter_cfg.supports_query)),
        supports_eval=bool(capabilities.get("eval", adapter_cfg.supports_eval)),
        supports_build=bool(capabilities.get("build", adapter_cfg.supports_build)),
        supports_materialize=bool(
            capabilities.get("materialize", adapter_cfg.supports_materialize)
        ),
        supports_smoke=bool(capabilities.get("smoke", adapter_cfg.supports_smoke)),
        supports_capture=bool(capabilities.get("capture", adapter_cfg.supports_capture)),
        supports_verify=bool(capabilities.get("verify", adapter_cfg.supports_verify)),
        supports_validate=bool(capabilities.get("validate", adapter_cfg.supports_validate)),
        supports_migrate=bool(capabilities.get("migrate", adapter_cfg.supports_migrate)),
        supports_inspect=bool(capabilities.get("inspect", adapter_cfg.supports_inspect)),
    )
