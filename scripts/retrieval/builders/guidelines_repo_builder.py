from __future__ import annotations

import hashlib
import json
import sqlite3
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retrieval.build.incremental_refresh import (
    InventoryEntry,
    capture_cross_db_baseline,
    embedding_reuse_key,
    ensure_incremental_tables,
    estimate_embedding_impact,
    load_guideline_inventory,
    plan_inventory_delta,
    prepare_staged_db,
    promote_staged_db,
    record_embedding_reuse_audit,
    record_materialization_delta,
    replace_guideline_inventory,
    require_force_rebuild,
    validate_cross_db_non_regression,
    validate_staged_corpus,
    write_delta_report,
    write_operator_summary,
    write_promotion_provenance,
    write_refresh_contract_report,
)
from retrieval.build.reports import (
    validate_guidelines_repo_db,
    write_current_guidelines_repo_validation_report,
)
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


def _guideline_inventory_rows(
    guidelines: list[Any],
    *,
    ingested_at: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for guideline in sorted(guidelines, key=lambda row: row.guideline_id):
        rows.append(
            {
                "guideline_id": str(guideline.guideline_id),
                "source_file_path": str(guideline.source_file_path),
                "source_hash": str(guideline.source_hash),
                "metadata_hash": canonical_json_hash(
                    {"metadata_json": str(guideline.metadata_json)}
                ),
                "blocks_hash": canonical_json_hash({"blocks": list(guideline.blocks)}),
                "citations_hash": canonical_json_hash({"citations": list(guideline.citations)}),
                "bibliography_hash": canonical_json_hash(
                    {"bibliography": dict(guideline.bibliography)}
                ),
                "last_ingested_at": ingested_at,
            }
        )
    return rows


def _guideline_inventory_map(rows: list[dict[str, str]]) -> dict[str, InventoryEntry]:
    return {
        row["guideline_id"]: InventoryEntry(
            entry_id=row["guideline_id"],
            content_sha256=row["source_hash"],
            metadata_sha256=canonical_json_hash(
                {
                    "metadata_hash": row["metadata_hash"],
                    "blocks_hash": row["blocks_hash"],
                    "citations_hash": row["citations_hash"],
                    "bibliography_hash": row["bibliography_hash"],
                }
            ),
            parent_id=row["source_file_path"],
        )
        for row in rows
    }


def _raw_fls_id(guideline: Any) -> str:
    try:
        metadata = json.loads(str(guideline.metadata_json or "{}"))
    except json.JSONDecodeError:
        metadata = {}
    return str(metadata.get("fls", "") or "").strip()


def _delete_guidelines(connection: sqlite3.Connection, guideline_ids: list[str]) -> None:
    if not guideline_ids:
        return
    placeholders = ", ".join("?" for _ in guideline_ids)
    connection.execute(
        f"DELETE FROM guideline_citations WHERE guideline_id IN ({placeholders})",
        tuple(guideline_ids),
    )
    connection.execute(
        f"DELETE FROM guideline_bib_links WHERE guideline_id IN ({placeholders})",
        tuple(guideline_ids),
    )
    connection.execute(
        f"DELETE FROM guideline_exemplars WHERE guideline_id IN ({placeholders})",
        tuple(guideline_ids),
    )
    connection.execute(
        f"DELETE FROM guideline_blocks WHERE guideline_id IN ({placeholders})",
        tuple(guideline_ids),
    )
    connection.execute(
        f"DELETE FROM guideline_records WHERE guideline_id IN ({placeholders})",
        tuple(guideline_ids),
    )
    if _table_exists(connection, "guideline_fls_source_mappings"):
        connection.execute(
            f"DELETE FROM guideline_fls_source_mappings WHERE guideline_id IN ({placeholders})",
            tuple(guideline_ids),
        )
    if _table_exists(connection, "guideline_fls_resolution_overrides"):
        connection.execute(
            (
                "DELETE FROM guideline_fls_resolution_overrides "
                f"WHERE guideline_id IN ({placeholders})"
            ),
            tuple(guideline_ids),
        )
    if _table_exists(connection, "guideline_fls_resolution_candidates"):
        connection.execute(
            (
                "DELETE FROM guideline_fls_resolution_candidates "
                f"WHERE guideline_id IN ({placeholders})"
            ),
            tuple(guideline_ids),
        )
    connection.execute(
        """
        DELETE FROM guideline_bibliography
        WHERE bib_key NOT IN (SELECT DISTINCT bib_key FROM guideline_bib_links)
        """
    )


def _upsert_guideline(
    connection: sqlite3.Connection,
    *,
    guideline: Any,
    revision: str,
    fetched_at: str,
    exemplar_ids: set[str],
) -> None:
    quality = "known_good" if guideline.guideline_id in exemplar_ids else "mixed"
    connection.execute(
        """
        INSERT OR REPLACE INTO guideline_records(
            guideline_id, title, source_file_path, quality_label,
            metadata_json, export_topic, source_revision, source_hash, ingested_at
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
            (
                "INSERT OR REPLACE INTO guideline_blocks("
                "block_id, guideline_id, block_type, order_index, content"
                ") VALUES(?, ?, ?, ?, ?)"
            ),
            (block_id, guideline.guideline_id, block_type, order_index, block_content),
        )
    for citation in guideline.citations:
        block_index = int(citation["block_order_index"])
        citation_block_type = str(citation.get("block_type", "body")).strip() or "body"
        citation_order = int(citation["order_index"])
        block_id = f"{guideline.guideline_id}:{citation_block_type}:{block_index}"
        ref_target = str(citation["ref_target"]).strip()
        citation_seed = guideline.guideline_id + block_id + ref_target + str(citation_order)
        citation_id = f"cite::{hashlib.sha256(citation_seed.encode('utf-8')).hexdigest()}"
        connection.execute(
            (
                "INSERT OR REPLACE INTO guideline_citations("
                "citation_id, guideline_id, block_id, ref_target, order_index"
                ") VALUES(?, ?, ?, ?, ?)"
            ),
            (citation_id, guideline.guideline_id, block_id, ref_target, citation_order),
        )
    for bib_key, bib_content in sorted(guideline.bibliography.items()):
        connection.execute(
            (
                "INSERT OR REPLACE INTO guideline_bibliography("
                "bib_key, content, source_file_path"
                ") VALUES(?, ?, ?)"
            ),
            (bib_key, bib_content, guideline.source_file_path),
        )
        connection.execute(
            ("INSERT OR REPLACE INTO guideline_bib_links(guideline_id, bib_key) VALUES(?, ?)"),
            (guideline.guideline_id, bib_key),
        )
    if guideline.guideline_id in exemplar_ids:
        connection.execute(
            (
                "INSERT OR REPLACE INTO guideline_exemplars("
                "guideline_id, added_at, rationale"
                ") VALUES(?, ?, ?)"
            ),
            (guideline.guideline_id, fetched_at, "configured_exemplar"),
        )
    if _table_exists(connection, "guideline_fls_source_mappings"):
        raw_fls_id = _raw_fls_id(guideline)
        connection.execute(
            """
            INSERT OR REPLACE INTO guideline_fls_source_mappings(
                guideline_id, source_file_path, raw_fls_id, raw_fls_present,
                source_revision, source_hash, last_ingested_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guideline.guideline_id,
                guideline.source_file_path,
                raw_fls_id,
                1 if raw_fls_id else 0,
                revision,
                guideline.source_hash,
                fetched_at,
            ),
        )


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


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
    inventory_rows = _guideline_inventory_rows(bundle.guidelines, ingested_at=fetched_at)
    incoming_inventory = _guideline_inventory_map(inventory_rows)
    use_incremental = bool(getattr(args, "incremental", False))
    force_rebuild = bool(getattr(args, "force_rebuild", False))
    fallback_in_progress = bool(getattr(args, "_fallback_in_progress", False))
    cross_db_baseline = capture_cross_db_baseline(root=root) if use_incremental else {}
    if bool(getattr(args, "refresh_derived_only", False)):
        raise RuntimeError("guidelines_repo builder does not yet support --refresh-derived-only")
    staged_root = (
        root / str(getattr(args, "staged_output_root", ".cache/sqlite_kb/staged"))
    ).resolve()
    promotion_root = (
        root / str(getattr(args, "promotion_root", ".cache/sqlite_kb/promotions"))
    ).resolve()
    run_id = f"guidelines_repo_incremental::{snapshot_id}"
    target_db_path = db_path
    promotion: dict[str, str] = {}
    if use_incremental:
        target_db_path, _ = prepare_staged_db(
            live_db_path=db_path,
            staged_root=staged_root,
            corpus="guidelines_repo",
            run_id=run_id,
        )

    latest_migration_id, _ = apply_pending_migrations(target_db_path, root=root)

    planned_delta = plan_inventory_delta(current={}, incoming=incoming_inventory)
    dry_run_report_path = write_delta_report(
        report_root=report_root,
        corpus="guidelines_repo",
        run_id=run_id,
        phase="pre_apply",
        payload={
            **planned_delta.as_dict(),
            "snapshot_id": snapshot_id,
            "db_path": str(target_db_path),
            "staged": use_incremental,
            "embedding_impact": estimate_embedding_impact(
                planned_delta=planned_delta,
                unchanged_chunk_ids=[],
                changed_chunk_ids=[],
            ),
        },
    )

    connection = sqlite3.connect(target_db_path)
    try:
        with connection:
            ensure_incremental_tables(connection)
            connection.execute("PRAGMA foreign_keys=ON")
            current_inventory = load_guideline_inventory(connection)
            planned_delta = plan_inventory_delta(
                current=current_inventory,
                incoming=incoming_inventory,
            )
            connection.execute(
                (
                    "INSERT OR REPLACE INTO snapshots("
                    "snapshot_id, commit_sha, source_url, fetched_at, sha256"
                    ") VALUES(?, ?, ?, ?, ?)"
                ),
                (
                    snapshot_id,
                    revision,
                    "https://github.com/rustfoundation/safety-critical-rust-coding-guidelines",
                    fetched_at,
                    source_hash,
                ),
            )
            if not use_incremental or not current_inventory:
                connection.execute("DELETE FROM guideline_citations")
                connection.execute("DELETE FROM guideline_bib_links")
                connection.execute("DELETE FROM guideline_exemplars")
                connection.execute("DELETE FROM guideline_blocks")
                connection.execute("DELETE FROM guideline_records")
                connection.execute("DELETE FROM guideline_bibliography")
                target_guidelines = sorted(bundle.guidelines, key=lambda row: row.guideline_id)
            else:
                changed = list(planned_delta.deleted) + list(planned_delta.updated)
                _delete_guidelines(connection, changed)
                target_guidelines = [
                    guideline
                    for guideline in sorted(bundle.guidelines, key=lambda row: row.guideline_id)
                    if guideline.guideline_id
                    in set(planned_delta.added) | set(planned_delta.updated)
                ]
            for guideline in target_guidelines:
                _upsert_guideline(
                    connection,
                    guideline=guideline,
                    revision=revision,
                    fetched_at=fetched_at,
                    exemplar_ids=exemplar_ids,
                )
            connection.execute(
                """
                DELETE FROM guideline_bibliography
                WHERE bib_key NOT IN (SELECT DISTINCT bib_key FROM guideline_bib_links)
                """
            )
            replace_guideline_inventory(connection, inventory_rows=inventory_rows)
            record_materialization_delta(
                connection,
                run_id=run_id,
                corpus="guidelines_repo",
                mode="incremental" if use_incremental else "full_rebuild",
                base_snapshot_id="",
                target_snapshot_id=snapshot_id,
                delta_payload={
                    **planned_delta.as_dict(),
                    "snapshot_id": snapshot_id,
                    "guidelines": len(bundle.guidelines),
                },
            )
            record_embedding_reuse_audit(
                connection,
                run_id=run_id,
                corpus="guidelines_repo",
                model_fingerprint=embedding_reuse_key(
                    stable_id="guidelines_repo",
                    content_sha256=source_hash,
                    model_id="none",
                    embed_version="v1",
                ),
                reused_count=0,
                recomputed_count=0,
            )
    finally:
        connection.close()

    delta_report_path = write_delta_report(
        report_root=report_root,
        corpus="guidelines_repo",
        run_id=run_id,
        payload={
            **planned_delta.as_dict(),
            "snapshot_id": snapshot_id,
            "db_path": str(target_db_path),
            "staged": use_incremental,
        },
    )
    dry_run_report_path = write_delta_report(
        report_root=report_root,
        corpus="guidelines_repo",
        run_id=run_id,
        phase="pre_apply",
        payload={
            **planned_delta.as_dict(),
            "snapshot_id": snapshot_id,
            "db_path": str(target_db_path),
            "staged": use_incremental,
            "embedding_impact": estimate_embedding_impact(
                planned_delta=planned_delta,
                unchanged_chunk_ids=[],
                changed_chunk_ids=[],
            ),
        },
    )
    refresh_contract_path = write_refresh_contract_report(
        report_root=report_root,
        corpus="guidelines_repo",
        run_id=run_id,
    )

    validation_report = validate_guidelines_repo_db(target_db_path)
    if not validation_report["passed"]:
        raise RuntimeError(f"guidelines_repo validation failed: {validation_report['failures']}")
    if use_incremental:
        try:
            stage_reports = validate_staged_corpus(
                corpus="guidelines_repo",
                staged_db_path=target_db_path,
            )
            cross_db_report_path = validate_cross_db_non_regression(
                root=root,
                report_root=report_root,
                run_id=run_id,
                target_corpus="guidelines_repo",
                baseline=cross_db_baseline,
                additional_stage_reports=stage_reports,
                target_staged_db_path=target_db_path,
            )
            promotion = promote_staged_db(
                live_db_path=db_path,
                staged_db_path=target_db_path,
                promotion_root=promotion_root,
                corpus="guidelines_repo",
                run_id=run_id,
            )
            promotion_provenance_path = write_promotion_provenance(
                report_root=report_root,
                corpus="guidelines_repo",
                run_id=run_id,
                payload={
                    "run_id": run_id,
                    "corpus": "guidelines_repo",
                    "validated_at": _utc_now(),
                    "validation_reports": stage_reports,
                    "refresh_contract_path": str(refresh_contract_path),
                    "cross_db_report_path": str(cross_db_report_path),
                    "promotion": promotion,
                },
            )
            promotion["promotion_provenance_path"] = str(promotion_provenance_path)
            operator_summary_path = write_operator_summary(
                report_root=report_root,
                corpus="guidelines_repo",
                run_id=run_id,
                payload={
                    "corpus": "guidelines_repo",
                    "run_id": run_id,
                    "status": "promoted",
                    "dry_run_report_path": str(dry_run_report_path),
                    "delta_report_path": str(delta_report_path),
                    "refresh_contract_path": str(refresh_contract_path),
                    "cross_db_report_path": str(cross_db_report_path),
                    "promotion_provenance_path": str(promotion_provenance_path),
                    "validation_kinds": [item["kind"] for item in stage_reports],
                },
            )
            promotion["operator_summary_path"] = str(operator_summary_path)
        except Exception as exc:
            if fallback_in_progress:
                raise
            require_force_rebuild(
                corpus="guidelines_repo",
                reason=str(exc),
                force_rebuild=force_rebuild,
            )
            fallback_args = Namespace(**vars(args))
            fallback_args.incremental = False
            fallback_args._fallback_in_progress = True
            return run_guidelines_repo_build(args=fallback_args, root=root)

    model_fingerprint = canonical_json_hash(
        {
            "embed_model_id": "Qwen/Qwen3-Embedding-4B",
            "reranker_model_id": "BAAI/bge-reranker-v2-m3",
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
            "details": {
                "source_url": "https://github.com/rustfoundation/safety-critical-rust-coding-guidelines"
            },
        },
        schema_migration_id=latest_migration_id,
        ingest_strategy="guidelines_artifacts_v1",
        ingest_strategy_version="1",
        ingest_params={
            "target_min_tokens": int(getattr(args, "chunk_target_min_tokens", 150)),
            "target_max_tokens": int(getattr(args, "chunk_target_max_tokens", 500)),
            "overlap_percent": float(getattr(args, "chunk_overlap_percent", 0.0)),
        },
        retrieval_profile_id="guidelines_repo_control",
        eval_policy_id="guidelines_repo",
        model_fingerprint=model_fingerprint,
        allow_provenance_mismatch=False,
    )
    write_current_guidelines_repo_validation_report(
        report_root=report_root,
        payload=validate_guidelines_repo_db(db_path),
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
        "validation_report": validation_report,
        "delta_report_path": str(delta_report_path),
        "dry_run_report_path": str(dry_run_report_path),
        "refresh_contract_path": str(refresh_contract_path),
        "incremental": use_incremental,
        "promotion": promotion,
    }
