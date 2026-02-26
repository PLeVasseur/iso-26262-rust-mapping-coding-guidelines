from __future__ import annotations

import hashlib
import json
import sqlite3
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retrieval.core.provenance import (
    apply_pending_migrations,
    canonical_json_hash,
    record_pipeline_run,
)
from retrieval.guidelines.build_runner import run_guidelines_build
from retrieval.ingest.registry import resolve_ingest_strategy


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_repo_root(args: Namespace, root: Path) -> Path:
    raw = str(getattr(args, "guidelines_repo_root", "")).strip()
    if not raw:
        raise RuntimeError("missing_required_flag::--guidelines-repo-root")
    path = Path(raw)
    if not path.is_absolute():
        path = (root / path).resolve()
    return path


def run_guidelines_repo_build(*, args: Namespace, root: Path) -> dict[str, Any]:
    repo_root = _resolve_repo_root(args, root)
    if not repo_root.exists():
        raise RuntimeError(f"guidelines_repo_root_not_found::{repo_root}")

    db_path = Path(str(getattr(args, "db_path", "")).strip())
    if not db_path.is_absolute():
        db_path = (root / db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    report_root = Path(str(getattr(args, "report_root", "")).strip())
    if not report_root.is_absolute():
        report_root = (root / report_root).resolve()
    report_root.mkdir(parents=True, exist_ok=True)

    assume_built = bool(getattr(args, "assume_built", False))
    if not assume_built:
        code, stdout, stderr, versions = run_guidelines_build(repo_root=repo_root, offline=True)
        run_dir = report_root / "build"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "stdout.log").write_text(stdout, encoding="utf-8")
        (run_dir / "stderr.log").write_text(stderr, encoding="utf-8")
        (run_dir / "versions.log").write_text("\n".join(versions) + "\n", encoding="utf-8")
        if code != 0:
            raise RuntimeError(f"guidelines_repo_build_failed::{repo_root}")

    strategy = resolve_ingest_strategy(
        str(getattr(args, "ingest_strategy", "guidelines_artifacts_v1"))
    )
    parse_artifacts = getattr(strategy, "parse_artifacts", None)
    if parse_artifacts is None:
        raise RuntimeError("ingest_strategy_missing_parse_artifacts")

    needs_contract = _read_json(root / "contracts" / "rf_needs_json.contract.json")
    ids_contract = _read_json(root / "contracts" / "rf_guidelines_ids.contract.json")
    bundle = parse_artifacts(
        repo_root=repo_root, needs_contract=needs_contract, ids_contract=ids_contract
    )

    latest_migration_id, _ = apply_pending_migrations(db_path, root=root)

    revision = str(getattr(args, "guidelines_repo_revision", "")).strip() or str(
        bundle.source_revision
    )
    snapshot_id = f"guidelines-repo-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    fetched_at = _utc_now()
    source_hash = str(bundle.source_hash)
    exemplar_ids = {
        str(value).strip()
        for value in list(getattr(args, "guidelines_exemplar_ids", []) or [])
        if str(value).strip()
    }

    connection = sqlite3.connect(db_path)
    try:
        with connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                "INSERT OR REPLACE INTO snapshots(snapshot_id, commit_sha, source_url, fetched_at, sha256) VALUES(?, ?, ?, ?, ?)",
                (
                    snapshot_id,
                    revision,
                    "https://github.com/rustfoundation/safety-critical-rust-coding-guidelines",
                    fetched_at,
                    source_hash,
                ),
            )

            connection.execute("DELETE FROM guideline_citations")
            connection.execute("DELETE FROM guideline_bib_links")
            connection.execute("DELETE FROM guideline_exemplars")
            connection.execute("DELETE FROM guideline_blocks")
            connection.execute("DELETE FROM guideline_records")

            seen_bib: set[str] = set()
            total_blocks = 0
            total_citations = 0
            for guideline in sorted(bundle.guidelines, key=lambda row: row.guideline_id):
                quality = "known_good" if guideline.guideline_id in exemplar_ids else "mixed"
                connection.execute(
                    """
                    INSERT INTO guideline_records(
                        guideline_id, title, source_file_path, quality_label,
                        metadata_json,
                        export_topic, source_revision, source_hash, ingested_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        guideline.guideline_id,
                        guideline.title,
                        guideline.source_file_path,
                        quality,
                        guideline.metadata_json,
                        guideline.export_topic,
                        revision,
                        guideline.source_hash,
                        fetched_at,
                    ),
                )
                for order_index, block in enumerate(guideline.blocks, start=1):
                    block_type = str(block.get("block_type", "body")).strip() or "body"
                    block_content = str(block.get("content", "")).strip()
                    block_id = f"{guideline.guideline_id}:{block_type}:{order_index}"
                    connection.execute(
                        "INSERT INTO guideline_blocks(block_id, guideline_id, block_type, order_index, content) VALUES(?, ?, ?, ?, ?)",
                        (block_id, guideline.guideline_id, block_type, order_index, block_content),
                    )
                    total_blocks += 1

                for citation in guideline.citations:
                    block_index = int(citation["block_order_index"])
                    citation_block_type = str(citation.get("block_type", "body")).strip() or "body"
                    citation_order = int(citation["order_index"])
                    block_id = f"{guideline.guideline_id}:{citation_block_type}:{block_index}"
                    ref_target = str(citation["ref_target"]).strip()
                    citation_id = f"cite::{hashlib.sha256((guideline.guideline_id + block_id + ref_target + str(citation_order)).encode('utf-8')).hexdigest()}"
                    connection.execute(
                        "INSERT OR REPLACE INTO guideline_citations(citation_id, guideline_id, block_id, ref_target, order_index) VALUES(?, ?, ?, ?, ?)",
                        (citation_id, guideline.guideline_id, block_id, ref_target, citation_order),
                    )
                    total_citations += 1

                for bib_key, bib_content in sorted(guideline.bibliography.items()):
                    if bib_key not in seen_bib:
                        connection.execute(
                            "INSERT OR REPLACE INTO guideline_bibliography(bib_key, content, source_file_path) VALUES(?, ?, ?)",
                            (bib_key, bib_content, guideline.source_file_path),
                        )
                        seen_bib.add(bib_key)
                    connection.execute(
                        "INSERT OR REPLACE INTO guideline_bib_links(guideline_id, bib_key) VALUES(?, ?)",
                        (guideline.guideline_id, bib_key),
                    )

                if guideline.guideline_id in exemplar_ids:
                    connection.execute(
                        "INSERT OR REPLACE INTO guideline_exemplars(guideline_id, added_at, rationale) VALUES(?, ?, ?)",
                        (guideline.guideline_id, fetched_at, "configured_exemplar"),
                    )
    finally:
        connection.close()

    model_fingerprint = canonical_json_hash(
        {
            "embed_model_id": "none",
            "reranker_model_id": "none",
            "embedding_dim": 0,
        }
    )
    pipeline_fingerprint = record_pipeline_run(
        db_path=db_path,
        run_id=f"build::{snapshot_id}",
        corpus="guidelines_repo",
        source_state={
            "source_revision": revision,
            "source_fingerprint": source_hash,
            "source_timestamp": fetched_at,
            "details": {"source_url": str(repo_root)},
        },
        schema_migration_id=latest_migration_id,
        ingest_strategy="guidelines_artifacts_v1",
        ingest_strategy_version="1",
        ingest_params={"assume_built": assume_built},
        retrieval_profile_id="guidelines_repo_control",
        eval_policy_id="guidelines_repo",
        model_fingerprint=model_fingerprint,
        allow_provenance_mismatch=False,
    )

    return {
        "corpus": "guidelines_repo",
        "db_path": str(db_path),
        "repo_root": str(repo_root),
        "source_revision": revision,
        "source_hash": source_hash,
        "guidelines": len(bundle.guidelines),
        "warnings": list(bundle.warnings),
        "pipeline_fingerprint": pipeline_fingerprint,
    }
