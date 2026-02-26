from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from pathlib import Path

import yaml

from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.services._invoke import run_main
from retrieval.services.capability import emit_unsupported
from sqlite_export_rst import main as export_main


def run(args: Namespace, *, root: Path) -> int:
    defaults = load_corpus_runtime_defaults(root=root, corpus=str(args.corpus))
    if defaults.corpus != "guidelines_repo":
        return emit_unsupported(
            corpus=defaults.corpus,
            operation="export-rst",
            reason="export-rst currently enabled only for guidelines_repo",
        )

    cfg_path = root / "config" / "corpora" / "guidelines_repo.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    if not isinstance(cfg, dict):
        cfg = {}
    sources_raw = cfg.get("sources") if isinstance(cfg, dict) else {}
    sources: dict[str, object] = sources_raw if isinstance(sources_raw, dict) else {}
    repo_root_raw = str(sources.get("guidelines_repo_root", "")).strip()
    repo_root = Path(repo_root_raw)
    if not repo_root.is_absolute():
        repo_root = (root / repo_root).resolve()

    chapter_root = repo_root / "src" / "coding-guidelines"
    chapter_root.mkdir(parents=True, exist_ok=True)
    argv = [
        "sqlite_export_rst.py",
        "--db-path",
        str(defaults.db_path),
        "--output-root",
        str(chapter_root),
        "--repo-root",
        str(repo_root),
    ]
    argv.extend(list(args.extra_args or []))
    status = run_main(export_main, argv)
    if status != 0:
        return status

    connection = sqlite3.connect(defaults.db_path)
    try:
        row = connection.execute(
            "SELECT commit_sha, fetched_at FROM snapshots ORDER BY fetched_at DESC LIMIT 1"
        ).fetchone()
        source_revision = str(row[0]) if row else "unknown"
        file_count = len(list(chapter_root.glob("**/*.rst")))
        digest = ""
        payload = {
            "files": sorted(
                [
                    {
                        "path": str(path.relative_to(chapter_root)),
                        "sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
                    }
                    for path in chapter_root.glob("**/*.rst")
                ],
                key=lambda row: row["path"],
            )
        }
        digest = (
            __import__("hashlib")
            .sha256(json.dumps(payload, sort_keys=True).encode("utf-8"))
            .hexdigest()
        )
        connection.execute(
            "INSERT OR REPLACE INTO guideline_export_runs(run_id, corpus, source_revision, output_root, file_count, output_digest, created_at) VALUES(?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                f"export::{source_revision}",
                defaults.corpus,
                source_revision,
                str(chapter_root),
                int(file_count),
                digest,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return status
