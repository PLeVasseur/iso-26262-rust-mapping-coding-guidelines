from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import yaml

from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.services._invoke import run_main
from retrieval.services.capability import emit_unsupported
from sqlite_build import main as build_main


def run(args: Namespace, *, root: Path) -> int:
    defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))
    if not defaults.supports_build:
        return emit_unsupported(
            corpus=defaults.corpus,
            operation="build",
            reason="corpus configuration disables build",
        )
    argv = [
        "sqlite_build.py",
        "--corpus",
        defaults.corpus,
        "--db-path",
        str(defaults.db_path),
        "--report-root",
        str(defaults.report_root),
        "--ingest-strategy",
        str(defaults.ingest_strategy),
        "--chunk-target-min-tokens",
        str(defaults.chunk_target_min_tokens),
        "--chunk-target-max-tokens",
        str(defaults.chunk_target_max_tokens),
        "--chunk-overlap-percent",
        str(defaults.chunk_overlap_percent),
    ]
    if defaults.corpus == "guidelines_repo":
        cfg_path = root / "config" / "corpora" / "guidelines_repo.yaml"
        payload = {}
        if cfg_path.exists():
            payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        sources = payload.get("sources") if isinstance(payload, dict) else {}
        if not isinstance(sources, dict):
            sources = {}
        repo_root = str(sources.get("guidelines_repo_root", "")).strip()
        repo_revision = str(sources.get("guidelines_repo_revision", "")).strip()
        exemplar_ids = sources.get("known_good_exemplar_ids")
        if repo_root:
            argv.extend(["--guidelines-repo-root", repo_root])
        if repo_revision:
            argv.extend(["--guidelines-repo-revision", repo_revision])
        if isinstance(exemplar_ids, list):
            for exemplar_id in exemplar_ids:
                normalized = str(exemplar_id).strip()
                if normalized:
                    argv.extend(["--guidelines-exemplar-id", normalized])
    argv.extend(list(args.extra_args or []))
    return run_main(build_main, argv)
