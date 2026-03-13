from __future__ import annotations

import shutil
import sqlite3
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path

from retrieval.build.core_docs_incremental import (
    build_core_docs_materialized_view,
    delete_core_docs_documents,
    refresh_core_docs_fts,
    upsert_core_docs_document,
)
from retrieval.build.incremental_refresh import (
    audit_preserved_chunk_embeddings,
    capture_cross_db_baseline,
    embedding_reuse_key,
    ensure_incremental_tables,
    estimate_embedding_impact,
    load_source_inventory_documents,
    plan_inventory_delta,
    prepare_staged_db,
    promote_staged_db,
    record_embedding_reuse_audit,
    record_materialization_delta,
    replace_source_inventory,
    require_force_rebuild,
    utc_now,
    validate_cross_db_non_regression,
    validate_staged_corpus,
    write_delta_report,
    write_operator_summary,
    write_promotion_provenance,
    write_refresh_contract_report,
)
from retrieval.build.reports import (
    validate_chunk_first_db,
    write_chunk_first_validation_report,
    write_current_chunk_first_validation_report,
)
from retrieval.build.schema import initialize_schema
from retrieval.build.table1_rows import resolve_table1_rows as _resolve_table1_rows
from retrieval.core.provenance import (
    apply_pending_migrations,
    canonical_json_hash,
    compute_source_state_from_db,
    record_pipeline_run,
)
from retrieval.core_docs.rustdoc_extract import (
    CANONICAL_TARGET,
    OVERLAY_ITEM_CAP,
    TARGET_MATRIX,
    ParsedItem,
)
from retrieval.core_docs.rustdoc_extract import (
    generate_rustdoc_json as _generate_rustdoc_json,
)
from retrieval.core_docs.rustdoc_extract import (
    load_parsed_items as _load_parsed_items,
)
from retrieval.core_docs.rustdoc_extract import (
    sha256_text as _sha256_text,
)
from retrieval.core_docs.rustdoc_extract import (
    split_chunks as _split_chunks,
)
from retrieval.core_docs.rustdoc_extract import (
    target_cfg as _target_cfg,
)
from retrieval.core_docs.rustdoc_extract import (
    toolchain_version as _toolchain_version,
)
from retrieval.core_docs.rustdoc_extract import (
    utc_now as _utc_now,
)
from retrieval.core_docs.rustdoc_extract import (
    write_manifest as _write_manifest,
)
from retrieval.ingest.registry import resolve_ingest_strategy
from retrieval.operations.build import (
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_MODEL_ID,
    DEFAULT_EXTRACTOR_DB,
    DEFAULT_RERANKER_MODEL_ID,
    DEFAULT_TABLE_NODE_ID,
)
from retrieval.services.ws7_prework_closure import maybe_refresh_ws7_prework_closure_packet


def _insert_table1_rows(
    connection: sqlite3.Connection, table_rows: list[dict[str, object]]
) -> None:
    for row in table_rows:
        row_node_id = str(row["row_node_id"])
        row_idx = int(str(row["row_idx"]))
        row_marker = str(row["row_marker"])
        requirement_text = str(row["requirement_text"])
        connection.execute(
            """
            INSERT INTO table1_rows(row_node_id, row_idx, row_marker, table_ref, requirement_text)
            VALUES(?, ?, ?, ?, ?)
            """,
            (row_node_id, row_idx, row_marker, "ISO 26262-6:2018 Table 1", requirement_text),
        )
        row_terms: list[str] = []
        raw_terms = row.get("row_profile_terms")
        if isinstance(raw_terms, list):
            row_terms = [str(term).strip().lower() for term in raw_terms if str(term).strip()]
        if not row_terms:
            row_terms = [token for token in requirement_text.lower().split() if len(token) >= 4][:8]
        for idx, term in enumerate(row_terms, start=1):
            connection.execute(
                """
                INSERT INTO table1_row_profile_terms(row_node_id, term_order, term, term_source)
                VALUES(?, ?, ?, ?)
                """,
                (row_node_id, idx, term, "core-docs-rustdoc-v1"),
            )


def _read_latest_snapshot_id(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT snapshot_id FROM snapshots ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row and row[0] is not None else ""


def run_core_docs_build(*, args: Namespace, root: Path) -> dict[str, object]:
    extractor_db = (
        Path(str(getattr(args, "extractor_db", DEFAULT_EXTRACTOR_DB))).expanduser().resolve()
    )
    table_node_id = str(getattr(args, "table_node_id", DEFAULT_TABLE_NODE_ID))
    db_path = (root / str(args.db_path)).resolve()
    report_root = (
        root / str(getattr(args, "report_root", ".cache/sqlite_kb/reports/core_docs"))
    ).resolve()
    report_root.mkdir(parents=True, exist_ok=True)

    source_revision = (
        str(getattr(args, "reference_revision", "") or "").strip() or "rust-1.83.1-content"
    )
    toolchain = str(
        getattr(args, "extractor_toolchain", "") or "nightly-aarch64-apple-darwin"
    ).strip()
    fetched_at = _utc_now()
    snapshot_id = f"core-docs-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"

    artifact_root = (
        root / ".cache" / "sqlite_kb" / "sources" / "core-docs" / "rustdoc-json" / source_revision
    )
    artifact_root.mkdir(parents=True, exist_ok=True)
    toolchain_version = _toolchain_version(toolchain)

    all_items: list[ParsedItem] = []
    for target in TARGET_MATRIX:
        generated = _generate_rustdoc_json(toolchain, target)
        target_dir = artifact_root / target
        target_dir.mkdir(parents=True, exist_ok=True)
        copied = target_dir / "core.json"
        shutil.copy2(generated, copied)
        _write_manifest(
            target_dir / "manifest.sha256",
            toolchain_version_value=toolchain_version,
            target=target,
            source_revision=source_revision,
        )
        parsed_items = _load_parsed_items(copied, _target_cfg(toolchain, target))
        if target != CANONICAL_TARGET and len(parsed_items) > OVERLAY_ITEM_CAP:
            parsed_items = parsed_items[:OVERLAY_ITEM_CAP]
        all_items.extend(parsed_items)

    table_rows = _resolve_table1_rows(extractor_db=extractor_db, table_node_id=table_node_id)
    if not table_rows:
        raise RuntimeError("No Table 1 rows resolved for core_docs build")
    if not all_items:
        raise RuntimeError("No rustdoc items extracted for core_docs build")

    strategy = resolve_ingest_strategy(
        str(getattr(args, "ingest_strategy", "core_docs_rustdoc_v1"))
    )
    run_id = f"core_docs_incremental::{snapshot_id}"
    staged_root = (
        root / str(getattr(args, "staged_output_root", ".cache/sqlite_kb/staged"))
    ).resolve()
    promotion_root = (
        root / str(getattr(args, "promotion_root", ".cache/sqlite_kb/promotions"))
    ).resolve()
    use_incremental = bool(getattr(args, "incremental", False))
    force_rebuild = bool(getattr(args, "force_rebuild", False))
    fallback_in_progress = bool(getattr(args, "_fallback_in_progress", False))
    cross_db_baseline = capture_cross_db_baseline(root=root) if use_incremental else {}
    if bool(getattr(args, "refresh_derived_only", False)):
        raise RuntimeError("core_docs builder does not yet support --refresh-derived-only")

    materialized = build_core_docs_materialized_view(
        all_items=all_items,
        strategy=strategy,
        snapshot_id=snapshot_id,
        fetched_at=fetched_at,
        source_revision=source_revision,
        chunk_target_min_tokens=int(getattr(args, "chunk_target_min_tokens", 150)),
        chunk_target_max_tokens=int(getattr(args, "chunk_target_max_tokens", 500)),
        split_chunks=_split_chunks,
    )

    target_db_path = db_path
    promotion: dict[str, str] = {}
    if use_incremental:
        target_db_path, _ = prepare_staged_db(
            live_db_path=db_path,
            staged_root=staged_root,
            corpus="core_docs",
            run_id=run_id,
        )

    if not target_db_path.exists():
        target_db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(target_db_path)
        try:
            initialize_schema(connection)
            connection.commit()
        finally:
            connection.close()

    latest_migration_id, _ = apply_pending_migrations(target_db_path, root=root)

    connection = sqlite3.connect(target_db_path)
    try:
        ensure_incremental_tables(connection)
        base_snapshot_id = _read_latest_snapshot_id(connection)
        current_inventory = load_source_inventory_documents(connection, corpus="core_docs")
        planned_delta = plan_inventory_delta(
            current=current_inventory,
            incoming=materialized["document_inventory"],
        )
        unchanged_docs = set(planned_delta.unchanged)
        unchanged_sections = {
            section_id
            for section_id, entry in materialized["section_inventory"].items()
            if entry.parent_id in unchanged_docs
        }
        unchanged_chunk_ids = [
            unit_id
            for (unit_kind, unit_id), entry in materialized["unit_inventory"].items()
            if unit_kind == "chunk" and entry.parent_id in unchanged_sections
        ]
        dry_run_report_path = write_delta_report(
            report_root=report_root,
            corpus="core_docs",
            run_id=run_id,
            phase="pre_apply",
            payload={
                **planned_delta.as_dict(),
                "snapshot_id": snapshot_id,
                "db_path": str(target_db_path),
                "staged": use_incremental,
                "embedding_impact": estimate_embedding_impact(
                    planned_delta=planned_delta,
                    unchanged_chunk_ids=unchanged_chunk_ids,
                    changed_chunk_ids=[
                        unit_id
                        for (unit_kind, unit_id), entry in materialized["unit_inventory"].items()
                        if unit_kind == "chunk" and entry.parent_id not in unchanged_sections
                    ],
                ),
            },
        )
        snapshot_sha = _sha256_text("::".join((snapshot_id, source_revision, fetched_at)))
        connection.execute(
            """
            INSERT INTO snapshots(snapshot_id, commit_sha, source_url, fetched_at, sha256)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                source_revision,
                "https://doc.rust-lang.org/core/",
                fetched_at,
                snapshot_sha,
            ),
        )
        if not use_incremental or not current_inventory:
            connection.execute("DELETE FROM core_docs_chunk_metadata")
            connection.execute("DELETE FROM chunk_spans")
            connection.execute("DELETE FROM chunks")
            connection.execute("DELETE FROM sections")
            connection.execute("DELETE FROM source_documents")
            connection.execute("DELETE FROM docs")
            connection.execute("DELETE FROM table1_row_profile_terms")
            connection.execute("DELETE FROM table1_rows")
            _insert_table1_rows(connection, table_rows)
            for document in materialized["docs"].values():
                upsert_core_docs_document(
                    connection,
                    snapshot_id=snapshot_id,
                    fetched_at=fetched_at,
                    source_revision=source_revision,
                    document=document,
                )
        else:
            delete_ids = list(planned_delta.deleted) + list(planned_delta.updated)
            delete_core_docs_documents(connection, delete_ids)
            for document in materialized["docs"].values():
                upsert_core_docs_document(
                    connection,
                    snapshot_id=snapshot_id,
                    fetched_at=fetched_at,
                    source_revision=source_revision,
                    document=document,
                )

        refresh_core_docs_fts(connection)
        replace_source_inventory(
            connection,
            corpus="core_docs",
            snapshot_id=snapshot_id,
            documents=materialized["document_inventory"],
            sections=materialized["section_inventory"],
            units=materialized["unit_inventory"],
            materialized_at=utc_now(),
        )
        record_materialization_delta(
            connection,
            run_id=run_id,
            corpus="core_docs",
            mode="incremental" if use_incremental else "full_rebuild",
            base_snapshot_id=base_snapshot_id,
            target_snapshot_id=snapshot_id,
            delta_payload={
                **planned_delta.as_dict(),
                "snapshot_id": snapshot_id,
                "force_rebuild": force_rebuild,
            },
        )
        embedding_audit = audit_preserved_chunk_embeddings(
            connection,
            corpus="core_docs",
            unchanged_chunk_ids=unchanged_chunk_ids if use_incremental else [],
        )
        record_embedding_reuse_audit(
            connection,
            run_id=run_id,
            corpus="core_docs",
            model_fingerprint=embedding_reuse_key(
                stable_id="core_docs",
                content_sha256=snapshot_sha,
                model_id=str(getattr(args, "embedding_model_id", DEFAULT_EMBEDDING_MODEL_ID)),
                embed_version="v1",
            ),
            reused_count=int(embedding_audit["reused_count"]),
            recomputed_count=int(embedding_audit["recomputed_count"]),
        )
        connection.commit()
    finally:
        connection.close()

    delta_report_path = write_delta_report(
        report_root=report_root,
        corpus="core_docs",
        run_id=run_id,
        payload={
            **planned_delta.as_dict(),
            "snapshot_id": snapshot_id,
            "db_path": str(target_db_path),
            "staged": use_incremental,
        },
    )
    refresh_contract_path = write_refresh_contract_report(
        report_root=report_root,
        corpus="core_docs",
        run_id=run_id,
    )

    if use_incremental:
        try:
            stage_reports = validate_staged_corpus(
                corpus="core_docs", staged_db_path=target_db_path
            )
            cross_db_report_path = validate_cross_db_non_regression(
                root=root,
                report_root=report_root,
                run_id=run_id,
                target_corpus="core_docs",
                baseline=cross_db_baseline,
                additional_stage_reports=stage_reports,
                target_staged_db_path=target_db_path,
            )
            promotion = promote_staged_db(
                live_db_path=db_path,
                staged_db_path=target_db_path,
                promotion_root=promotion_root,
                corpus="core_docs",
                run_id=run_id,
            )
            promotion_provenance_path = write_promotion_provenance(
                report_root=report_root,
                corpus="core_docs",
                run_id=run_id,
                payload={
                    "run_id": run_id,
                    "corpus": "core_docs",
                    "validated_at": utc_now(),
                    "validation_reports": stage_reports,
                    "refresh_contract_path": str(refresh_contract_path),
                    "cross_db_report_path": str(cross_db_report_path),
                    "promotion": promotion,
                },
            )
            promotion["promotion_provenance_path"] = str(promotion_provenance_path)
            operator_summary_path = write_operator_summary(
                report_root=report_root,
                corpus="core_docs",
                run_id=run_id,
                payload={
                    "corpus": "core_docs",
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
                corpus="core_docs",
                reason=str(exc),
                force_rebuild=force_rebuild,
            )
            fallback_args = Namespace(**vars(args))
            fallback_args.incremental = False
            fallback_args._fallback_in_progress = True
            return run_core_docs_build(args=fallback_args, root=root)

    source_state = compute_source_state_from_db(db_path)
    model_fingerprint = canonical_json_hash(
        {
            "embed_model_id": str(getattr(args, "embedding_model_id", DEFAULT_EMBEDDING_MODEL_ID)),
            "reranker_model_id": str(getattr(args, "reranker_model_id", DEFAULT_RERANKER_MODEL_ID)),
            "embedding_dim": int(getattr(args, "embedding_dim", DEFAULT_EMBEDDING_DIM)),
        }
    )
    fingerprint = record_pipeline_run(
        db_path=db_path,
        run_id=f"build::{snapshot_id}",
        corpus="core_docs",
        source_state=source_state,
        schema_migration_id=latest_migration_id,
        ingest_strategy="core_docs_rustdoc_v1",
        ingest_strategy_version="1",
        ingest_params={
            "target_min_tokens": int(getattr(args, "chunk_target_min_tokens", 150)),
            "target_max_tokens": int(getattr(args, "chunk_target_max_tokens", 500)),
        },
        retrieval_profile_id="core_docs_control",
        eval_policy_id="core_docs",
        model_fingerprint=model_fingerprint,
        allow_provenance_mismatch=bool(getattr(args, "allow_provenance_mismatch", False)),
    )
    chunk_first_report = validate_chunk_first_db(db_path, corpus="core_docs")
    chunk_first_report_path = write_chunk_first_validation_report(
        report_root=report_root,
        corpus="core_docs",
        snapshot_id=snapshot_id,
        payload=chunk_first_report,
    )
    write_current_chunk_first_validation_report(
        report_root=report_root,
        corpus="core_docs",
        payload=chunk_first_report,
    )
    maybe_refresh_ws7_prework_closure_packet(
        root=root,
        deferred_items=["WS7 staged runtime implementation"],
    )
    if not chunk_first_report["passed"]:
        raise RuntimeError(
            f"Chunk-first validation failed for core_docs.sqlite: {chunk_first_report['failures']}"
        )

    return {
        "corpus": "core_docs",
        "snapshot_id": snapshot_id,
        "db_path": str(db_path),
        "chunk_first_report_path": str(chunk_first_report_path),
        "delta_report_path": str(delta_report_path),
        "dry_run_report_path": str(dry_run_report_path),
        "refresh_contract_path": str(refresh_contract_path),
        "incremental": use_incremental,
        "promotion": promotion,
        "targets": list(TARGET_MATRIX),
        "items": len(all_items),
        "rows": len(table_rows),
        "pipeline_fingerprint": fingerprint,
    }
