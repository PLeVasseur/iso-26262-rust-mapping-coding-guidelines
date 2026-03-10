#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retrieval.build.artifacts import build_retrieval_artifacts
from retrieval.build.cli import parse_build_args
from retrieval.build.database_write import materialize_snapshot_db
from retrieval.build.incremental_refresh import (
    audit_preserved_chunk_embeddings,
    build_reference_inventory,
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
    validate_cross_db_non_regression,
    validate_staged_corpus,
    write_delta_report,
    write_operator_summary,
    write_promotion_provenance,
    write_refresh_contract_report,
)
from retrieval.build.persistence import (
    compute_snapshot_sha256 as _compute_snapshot_sha256,
)
from retrieval.build.persistence import (
    delete_reference_document_subtrees,
    refresh_reference_derived_tables,
    upsert_reference_source_rows,
)
from retrieval.build.reference_parsing import (
    extract_sections_and_statements as _extract_sections_and_statements,
)
from retrieval.build.reference_parsing import (
    load_source_documents as _load_source_documents,
)
from retrieval.build.reference_parsing import (
    parse_summary as _parse_summary,
)
from retrieval.build.reports import (
    load_manifest as _load_manifest,
)
from retrieval.build.reports import (
    read_previous_snapshot_path as _read_previous_snapshot_path,
)
from retrieval.build.reports import (
    update_manifest as _update_manifest,
)
from retrieval.build.reports import (
    validate_chunk_first_db,
    validate_rust_reference_db,
)
from retrieval.build.reports import (
    write_chunk_first_validation_report as _write_chunk_first_validation_report,
)
from retrieval.build.reports import (
    write_current_chunk_first_validation_report as _write_current_chunk_first_validation_report,
)
from retrieval.build.reports import (
    write_row_metadata_report as _write_row_metadata_report,
)
from retrieval.build.reports import (
    write_validation_report as _write_validation_report,
)
from retrieval.build.source_checkout import (
    resolve_reference_checkout as _resolve_reference_checkout,
)
from retrieval.core.provenance import (
    apply_pending_migrations,
    canonical_json_hash,
    compute_source_state_from_db,
    record_pipeline_run,
)
from retrieval.ingest.contracts import CleanInput
from retrieval.ingest.registry import resolve_ingest_strategy
from retrieval.services.ws7_prework_closure import maybe_refresh_ws7_prework_closure_packet

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def _resolve_default_extractor_db() -> Path:
    relative = Path(
        "personal/iso-26262-coding-standard-extraction/.cache/iso26262/iso26262_index.sqlite"
    )
    candidates = (
        Path.home() / relative,
        Path("/Users") / Path.home().name / relative,
        Path("/home") / Path.home().name / relative,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


DEFAULT_EXTRACTOR_DB = _resolve_default_extractor_db()
DEFAULT_TABLE_NODE_ID = "ISO26262-6-2018:node:table:table_1:001"
DEFAULT_REFERENCE_REPO_URL = "https://github.com/rust-lang/reference.git"
DEFAULT_REFERENCE_CACHE_DIR = ".cache/sqlite_kb/sources/rust-reference"
DEFAULT_REFERENCE_SOURCE_URL = "https://doc.rust-lang.org/reference/"
DEFAULT_EMBEDDING_MODEL_ID = "Qwen/Qwen3-Embedding-4B"
DEFAULT_EMBEDDING_MODEL_REVISION = "unspecified"
DEFAULT_EMBEDDING_MODEL_LICENSE = "unspecified"
DEFAULT_EMBEDDING_DIM = 0
DEFAULT_RERANKER_MODEL_ID = "BAAI/bge-reranker-v2-m3"
DEFAULT_RERANKER_MODEL_REVISION = "unspecified"
DEFAULT_RERANKER_MODEL_LICENSE = "unspecified"
DEFAULT_RETRIEVAL_MODE = "hybrid"
DEFAULT_SEMANTIC_PROFILE_VERSION = "semantic-hybrid-v1"
DEFAULT_RETRIEVAL_CORPUS = "chunk"
RETRIEVAL_CORPUS_VALUES = ("statement", "chunk")

CLEAN_TEXT_NORMALIZER_VERSION = "clean-v1"


def build_rust_reference_db(
    db_path: Path,
    snapshot_root: Path,
    manifest_path: Path,
    extractor_db: Path,
    table_node_id: str,
    reference_source_dir: Path | None = None,
    reference_cache_dir: Path | None = None,
    reference_repo_url: str = DEFAULT_REFERENCE_REPO_URL,
    reference_revision: str | None = None,
    skip_fetch: bool = False,
    report_root: Path | None = None,
    min_sections: int = 20,
    min_statements: int = 50,
    min_mechanisms: int = 6,
    retrieval_mode: str = DEFAULT_RETRIEVAL_MODE,
    retrieval_corpus: str = DEFAULT_RETRIEVAL_CORPUS,
    semantic_profile_version: str = DEFAULT_SEMANTIC_PROFILE_VERSION,
    embedding_model_id: str = DEFAULT_EMBEDDING_MODEL_ID,
    embedding_model_revision: str = DEFAULT_EMBEDDING_MODEL_REVISION,
    embedding_model_license: str = DEFAULT_EMBEDDING_MODEL_LICENSE,
    embedding_dim: int = DEFAULT_EMBEDDING_DIM,
    reranker_model_id: str = DEFAULT_RERANKER_MODEL_ID,
    reranker_model_revision: str = DEFAULT_RERANKER_MODEL_REVISION,
    reranker_model_license: str = DEFAULT_RERANKER_MODEL_LICENSE,
    ingest_strategy: str = "rust_md_v1",
    chunk_target_min_tokens: int = 150,
    chunk_target_max_tokens: int = 500,
    chunk_overlap_percent: float = 0.0,
    allow_provenance_mismatch: bool = False,
    incremental: bool = False,
    force_rebuild: bool = False,
    staged_output_root: Path | None = None,
    promotion_root: Path | None = None,
) -> dict[str, Any]:
    report_root = report_root or (db_path.parents[1] / "reports" / "rust_reference")
    reference_cache_dir = reference_cache_dir or (db_path.parents[1] / "sources" / "rust-reference")
    if retrieval_corpus not in RETRIEVAL_CORPUS_VALUES:
        raise ValueError(
            "Unsupported retrieval corpus "
            f"'{retrieval_corpus}'; expected one of {sorted(RETRIEVAL_CORPUS_VALUES)}"
        )
    if float(chunk_overlap_percent) < 0.0 or float(chunk_overlap_percent) > 0.45:
        raise ValueError(
            f"chunk_overlap_percent must be within [0.0, 0.45]; got {chunk_overlap_percent}"
        )
    if not str(reference_revision or "").strip():
        raise ValueError("Pinned source revision is required; pass --reference-revision explicitly")

    strategy = resolve_ingest_strategy(ingest_strategy)
    use_incremental = bool(incremental)
    cross_db_baseline = (
        capture_cross_db_baseline(root=Path(__file__).resolve().parents[3])
        if use_incremental
        else {}
    )
    staged_root = staged_output_root or (db_path.parents[1] / "staged")
    promotion_root = promotion_root or (db_path.parents[1] / "promotions")

    existing_manifest = _load_manifest(manifest_path)
    previous_snapshot_path = _read_previous_snapshot_path(
        existing_manifest, manifest_path=manifest_path
    )

    source_dir, commit_sha, source_fetched_at = _resolve_reference_checkout(
        reference_source_dir=reference_source_dir,
        reference_cache_dir=reference_cache_dir,
        reference_repo_url=reference_repo_url,
        reference_revision=reference_revision,
        skip_fetch=skip_fetch,
    )

    source_root = source_dir / "src"
    summary_entries = _parse_summary(source_root)
    documents, chapters = _load_source_documents(
        source_root=source_root,
        summary_entries=summary_entries,
        source_fetched_at=source_fetched_at,
        source_commit_sha=commit_sha,
    )

    snapshot_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    snapshot_id = f"rust-reference-{snapshot_stamp}"

    sections, statements = _extract_sections_and_statements(
        snapshot_id=snapshot_id,
        documents=documents,
        cleaner=lambda text: strategy.clean_text(
            CleanInput(raw_text=text, source_type="markdown", context={"corpus": "rust_reference"})
        ).cleaned_text,
    )
    artifacts = build_retrieval_artifacts(
        strategy=strategy,
        sections=sections,
        statements=statements,
        source_fetched_at=source_fetched_at,
        extractor_db=extractor_db,
        table_node_id=table_node_id,
        reference_source_url=DEFAULT_REFERENCE_SOURCE_URL,
        retrieval_mode=retrieval_mode,
        semantic_profile_version=semantic_profile_version,
        embedding_model_id=embedding_model_id,
        embedding_model_revision=embedding_model_revision,
        embedding_model_license=embedding_model_license,
        embedding_dim=embedding_dim,
        reranker_model_id=reranker_model_id,
        reranker_model_revision=reranker_model_revision,
        reranker_model_license=reranker_model_license,
        chunk_target_min_tokens=chunk_target_min_tokens,
        chunk_target_max_tokens=chunk_target_max_tokens,
        chunk_overlap_percent=chunk_overlap_percent,
    )
    chunks = artifacts["chunks"]
    chunk_spans = artifacts["chunk_spans"]
    mechanisms = artifacts["mechanisms"]
    mechanism_evidence = artifacts["mechanism_evidence"]
    semantic_models = artifacts["semantic_models"]
    table_rows = artifacts["table_rows"]
    semantic_corpus = artifacts["semantic_corpus"]
    row_verdicts = artifacts["row_verdicts"]
    row_mechanisms = artifacts["row_mechanisms"]
    row_mechanism_scores = artifacts["row_mechanism_scores"]
    counts = artifacts["counts"]
    document_inventory, section_inventory, unit_inventory = build_reference_inventory(
        documents=documents,
        sections=sections,
        statements=statements,
        chunks=chunks,
    )

    snapshot_sha256 = _compute_snapshot_sha256(
        commit_sha=commit_sha,
        documents=documents,
        sections=sections,
        statements=statements,
        chunks=chunks,
    )

    run_id = f"rust_reference_incremental::{snapshot_id}"
    target_db_path = db_path
    promotion: dict[str, str] = {}
    planned_delta = plan_inventory_delta(current={}, incoming=document_inventory)
    if use_incremental:
        target_db_path, _ = prepare_staged_db(
            live_db_path=db_path,
            staged_root=staged_root,
            corpus="rust_reference",
            run_id=run_id,
        )
        if target_db_path.exists():
            connection = sqlite3.connect(target_db_path)
            try:
                planned_delta = plan_inventory_delta(
                    current=load_source_inventory_documents(connection, corpus="rust_reference"),
                    incoming=document_inventory,
                )
            finally:
                connection.close()
    unchanged_doc_ids = set(planned_delta.unchanged)
    unchanged_section_ids = {
        section.section_id for section in sections if section.document_id in unchanged_doc_ids
    }
    unchanged_chunk_ids = [
        chunk.chunk_uid for chunk in chunks if chunk.section_id in unchanged_section_ids
    ]
    dry_run_report_path = write_delta_report(
        report_root=report_root,
        corpus="rust_reference",
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
                    chunk.chunk_uid
                    for chunk in chunks
                    if chunk.section_id not in unchanged_section_ids
                ],
            ),
        },
    )

    extractor_version = (
        f"sqlite-build-rust-reference-v7::{strategy.strategy_id}@{strategy.strategy_version}"
    )
    build_notes = (
        "chunk-first schema and deterministic block parsing via "
        f"{strategy.strategy_id}@{strategy.strategy_version}"
    )
    if not use_incremental:
        latest_migration_id, snapshot_db_path = materialize_snapshot_db(
            db_path=target_db_path,
            snapshot_root=snapshot_root,
            snapshot_id=snapshot_id,
            commit_sha=commit_sha,
            source_fetched_at=source_fetched_at,
            source_url=DEFAULT_REFERENCE_SOURCE_URL,
            snapshot_sha256=snapshot_sha256,
            chapters=chapters,
            documents=documents,
            sections=sections,
            statements=statements,
            chunks=chunks,
            chunk_spans=chunk_spans,
            mechanisms=mechanisms,
            mechanism_evidence=mechanism_evidence,
            table_rows=table_rows,
            row_verdicts=row_verdicts,
            row_mechanisms=row_mechanisms,
            semantic_models=semantic_models,
            semantic_corpus=semantic_corpus,
            row_mechanism_scores=row_mechanism_scores,
            extractor_version=extractor_version,
            build_notes=build_notes,
            project_root=Path(__file__).resolve().parents[3],
        )
    else:
        target_db_path.parent.mkdir(parents=True, exist_ok=True)
        if not target_db_path.exists():
            latest_migration_id, snapshot_db_path = materialize_snapshot_db(
                db_path=target_db_path,
                snapshot_root=snapshot_root,
                snapshot_id=snapshot_id,
                commit_sha=commit_sha,
                source_fetched_at=source_fetched_at,
                source_url=DEFAULT_REFERENCE_SOURCE_URL,
                snapshot_sha256=snapshot_sha256,
                chapters=chapters,
                documents=documents,
                sections=sections,
                statements=statements,
                chunks=chunks,
                chunk_spans=chunk_spans,
                mechanisms=mechanisms,
                mechanism_evidence=mechanism_evidence,
                table_rows=table_rows,
                row_verdicts=row_verdicts,
                row_mechanisms=row_mechanisms,
                semantic_models=semantic_models,
                semantic_corpus=semantic_corpus,
                row_mechanism_scores=row_mechanism_scores,
                extractor_version=extractor_version,
                build_notes=build_notes,
                project_root=Path(__file__).resolve().parents[3],
            )
        else:
            latest_migration_id = apply_pending_migrations(
                target_db_path, root=Path(__file__).resolve().parents[3]
            )[0]
            connection = sqlite3.connect(target_db_path)
            try:
                ensure_incremental_tables(connection)
                current_inventory = load_source_inventory_documents(
                    connection, corpus="rust_reference"
                )
                planned_delta = plan_inventory_delta(
                    current=current_inventory,
                    incoming=document_inventory,
                )
                changed_document_ids = sorted(
                    set(planned_delta.updated) | set(planned_delta.deleted)
                )
                insert_document_ids = sorted(set(planned_delta.added) | set(planned_delta.updated))
                delete_reference_document_subtrees(
                    connection,
                    document_ids=changed_document_ids,
                )
                for chapter in chapters:
                    connection.execute(
                        (
                            "INSERT OR REPLACE INTO chapters("
                            "chapter_id, title, order_index"
                            ") VALUES(?, ?, ?)"
                        ),
                        (chapter["chapter_id"], chapter["title"], int(chapter["order_index"])),
                    )
                connection.execute(
                    (
                        "INSERT OR REPLACE INTO snapshots("
                        "snapshot_id, commit_sha, source_url, fetched_at, sha256"
                        ") VALUES(?, ?, ?, ?, ?)"
                    ),
                    (
                        snapshot_id,
                        commit_sha,
                        DEFAULT_REFERENCE_SOURCE_URL,
                        source_fetched_at,
                        snapshot_sha256,
                    ),
                )
                changed_documents = [
                    doc for doc in documents if doc.document_id in insert_document_ids
                ]
                changed_sections = [
                    section for section in sections if section.document_id in insert_document_ids
                ]
                changed_section_ids = {section.section_id for section in changed_sections}
                changed_statements = [
                    statement
                    for statement in statements
                    if statement.section_id in changed_section_ids
                ]
                changed_chunks = [
                    chunk for chunk in chunks if chunk.section_id in changed_section_ids
                ]
                changed_chunk_ids = {chunk.chunk_uid for chunk in changed_chunks}
                changed_chunk_spans = [
                    chunk_span
                    for chunk_span in chunk_spans
                    if chunk_span.chunk_uid in changed_chunk_ids
                ]
                upsert_reference_source_rows(
                    connection,
                    snapshot_id=snapshot_id,
                    documents=changed_documents,
                    sections=changed_sections,
                    statements=changed_statements,
                    chunks=changed_chunks,
                    chunk_spans=changed_chunk_spans,
                )
                refresh_reference_derived_tables(
                    connection,
                    commit_sha=commit_sha,
                    fetched_at=source_fetched_at,
                    extractor_version=extractor_version,
                    build_notes=build_notes,
                    mechanisms=mechanisms,
                    mechanism_evidence=mechanism_evidence,
                    table_rows=table_rows,
                    row_verdicts=row_verdicts,
                    row_mechanisms=row_mechanisms,
                    semantic_models=semantic_models,
                    semantic_corpus=semantic_corpus,
                    row_mechanism_scores=row_mechanism_scores,
                )
                connection.commit()
            finally:
                connection.close()
            snapshot_root.mkdir(parents=True, exist_ok=True)
            snapshot_db_path = snapshot_root / f"{snapshot_id}.sqlite"
            __import__("shutil").copy2(target_db_path, snapshot_db_path)

    connection = sqlite3.connect(target_db_path)
    try:
        ensure_incremental_tables(connection)
        replace_source_inventory(
            connection,
            corpus="rust_reference",
            snapshot_id=snapshot_id,
            documents=document_inventory,
            sections=section_inventory,
            units=unit_inventory,
            materialized_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        record_materialization_delta(
            connection,
            run_id=run_id,
            corpus="rust_reference",
            mode="incremental" if use_incremental else "full_rebuild",
            base_snapshot_id=str(previous_snapshot_path or ""),
            target_snapshot_id=snapshot_id,
            delta_payload={
                **planned_delta.as_dict(),
                "snapshot_id": snapshot_id,
                "force_rebuild": force_rebuild,
            },
        )
        unchanged_doc_ids = set(planned_delta.unchanged)
        unchanged_section_ids = {
            section.section_id for section in sections if section.document_id in unchanged_doc_ids
        }
        unchanged_chunk_ids = [
            chunk.chunk_uid for chunk in chunks if chunk.section_id in unchanged_section_ids
        ]
        embedding_audit = audit_preserved_chunk_embeddings(
            connection,
            corpus="rust_reference",
            unchanged_chunk_ids=unchanged_chunk_ids if use_incremental else [],
        )
        record_embedding_reuse_audit(
            connection,
            run_id=run_id,
            corpus="rust_reference",
            model_fingerprint=embedding_reuse_key(
                stable_id="rust_reference",
                content_sha256=snapshot_sha256,
                model_id=str(embedding_model_id),
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
        corpus="rust_reference",
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
        corpus="rust_reference",
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
                    chunk.chunk_uid
                    for chunk in chunks
                    if chunk.section_id not in unchanged_section_ids
                ],
            ),
        },
    )
    refresh_contract_path = write_refresh_contract_report(
        report_root=report_root,
        corpus="rust_reference",
        run_id=run_id,
    )

    validation_report = validate_rust_reference_db(
        db_path=target_db_path,
        previous_snapshot_path=previous_snapshot_path,
        min_sections=min_sections,
        min_statements=min_statements,
        min_mechanisms=min_mechanisms,
    )
    validation_report.update(
        {
            "snapshot_id": snapshot_id,
            "commit_sha": commit_sha,
            "documents": len(documents),
            "chapters": len(chapters),
            "sections": len(sections),
            "statements": len(statements),
            "chunks": len(chunks),
            "mechanisms": len(mechanisms),
            "mechanism_evidence": len(mechanism_evidence),
            "source_fetched_at": source_fetched_at,
        }
    )
    report_path = _write_validation_report(
        report_root=report_root, snapshot_id=snapshot_id, payload=validation_report
    )
    chunk_first_report = validate_chunk_first_db(target_db_path, corpus="rust_reference")
    chunk_first_report_path = _write_chunk_first_validation_report(
        report_root=report_root,
        corpus="rust_reference",
        snapshot_id=snapshot_id,
        payload=chunk_first_report,
    )
    _write_current_chunk_first_validation_report(
        report_root=report_root,
        corpus="rust_reference",
        payload=chunk_first_report,
    )
    maybe_refresh_ws7_prework_closure_packet(
        root=Path(__file__).resolve().parents[3],
        deferred_items=["WS7 staged runtime implementation"],
    )
    row_metadata_report_path = _write_row_metadata_report(
        report_root=report_root,
        snapshot_id=snapshot_id,
        table_rows=table_rows,
    )
    if not validation_report["passed"]:
        raise RuntimeError(
            f"Validation failed for rust_reference.sqlite: {validation_report['failures']}"
        )
    if not chunk_first_report["passed"]:
        raise RuntimeError(
            "Chunk-first validation failed for rust_reference.sqlite: "
            f"{chunk_first_report['failures']}"
        )
    if use_incremental:
        try:
            stage_reports = validate_staged_corpus(
                corpus="rust_reference",
                staged_db_path=target_db_path,
                extra_validator=lambda path: validate_rust_reference_db(
                    db_path=path,
                    previous_snapshot_path=previous_snapshot_path,
                    min_sections=min_sections,
                    min_statements=min_statements,
                    min_mechanisms=min_mechanisms,
                ),
            )
            cross_db_report_path = validate_cross_db_non_regression(
                root=Path(__file__).resolve().parents[3],
                report_root=report_root,
                run_id=run_id,
                target_corpus="rust_reference",
                baseline=cross_db_baseline,
                additional_stage_reports=stage_reports,
                target_staged_db_path=target_db_path,
                target_extra_validator=lambda path: validate_rust_reference_db(
                    db_path=path,
                    previous_snapshot_path=previous_snapshot_path,
                    min_sections=min_sections,
                    min_statements=min_statements,
                    min_mechanisms=min_mechanisms,
                ),
            )
            promotion = promote_staged_db(
                live_db_path=db_path,
                staged_db_path=target_db_path,
                promotion_root=promotion_root,
                corpus="rust_reference",
                run_id=run_id,
            )
            promotion_provenance_path = write_promotion_provenance(
                report_root=report_root,
                corpus="rust_reference",
                run_id=run_id,
                payload={
                    "run_id": run_id,
                    "corpus": "rust_reference",
                    "validated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "validation_reports": stage_reports,
                    "refresh_contract_path": str(refresh_contract_path),
                    "cross_db_report_path": str(cross_db_report_path),
                    "promotion": promotion,
                },
            )
            promotion["promotion_provenance_path"] = str(promotion_provenance_path)
            operator_summary_path = write_operator_summary(
                report_root=report_root,
                corpus="rust_reference",
                run_id=run_id,
                payload={
                    "corpus": "rust_reference",
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
            require_force_rebuild(
                corpus="rust_reference",
                reason=str(exc),
                force_rebuild=force_rebuild,
            )
            return build_rust_reference_db(
                db_path=db_path,
                snapshot_root=snapshot_root,
                manifest_path=manifest_path,
                extractor_db=extractor_db,
                table_node_id=table_node_id,
                reference_source_dir=reference_source_dir,
                reference_revision=reference_revision,
                min_sections=min_sections,
                min_statements=min_statements,
                min_mechanisms=min_mechanisms,
                retrieval_mode=retrieval_mode,
                semantic_profile_version=semantic_profile_version,
                embedding_model_id=embedding_model_id,
                embedding_model_revision=embedding_model_revision,
                embedding_model_license=embedding_model_license,
                embedding_dim=embedding_dim,
                reranker_model_id=reranker_model_id,
                reranker_model_revision=reranker_model_revision,
                reranker_model_license=reranker_model_license,
                chunk_target_min_tokens=chunk_target_min_tokens,
                chunk_target_max_tokens=chunk_target_max_tokens,
                chunk_overlap_percent=chunk_overlap_percent,
                allow_provenance_mismatch=allow_provenance_mismatch,
                incremental=False,
                force_rebuild=False,
                staged_output_root=staged_output_root,
                promotion_root=promotion_root,
            )

    _update_manifest(
        manifest_path=manifest_path,
        snapshot_id=snapshot_id,
        current_db_path=db_path,
        snapshot_db_path=snapshot_db_path,
        commit_sha=commit_sha,
        source_fetched_at=source_fetched_at,
        source_url=DEFAULT_REFERENCE_SOURCE_URL,
        report_path=report_path,
        row_metadata_report_path=row_metadata_report_path,
        chunk_first_report_path=chunk_first_report_path,
        counts=counts,
        chunk_count=len(chunks),
        chunk_overlap_percent=float(chunk_overlap_percent),
        retrieval_mode=retrieval_mode,
        retrieval_corpus=retrieval_corpus,
        semantic_profile_version=semantic_profile_version,
        embedding_model_id=embedding_model_id,
        reranker_model_id=reranker_model_id,
        chunk_fts_mapping=validation_report.get("chunk_fts_mapping"),
    )

    source_state = compute_source_state_from_db(db_path)
    model_fingerprint = canonical_json_hash(
        {
            "embed_model_id": str(embedding_model_id),
            "reranker_model_id": str(reranker_model_id),
            "embedding_dim": int(embedding_dim),
        }
    )
    pipeline_fingerprint = record_pipeline_run(
        db_path=db_path,
        run_id=f"build::{snapshot_id}",
        corpus="rust_reference",
        source_state=source_state,
        schema_migration_id=latest_migration_id,
        ingest_strategy=strategy.strategy_id,
        ingest_strategy_version=strategy.strategy_version,
        ingest_params={
            "target_min_tokens": int(chunk_target_min_tokens),
            "target_max_tokens": int(chunk_target_max_tokens),
            "overlap_percent": float(chunk_overlap_percent),
        },
        retrieval_profile_id="rust_reference_control",
        eval_policy_id="rust_reference",
        model_fingerprint=model_fingerprint,
        allow_provenance_mismatch=bool(allow_provenance_mismatch),
    )

    return {
        "snapshot_id": snapshot_id,
        "commit_sha": commit_sha,
        "source_fetched_at": source_fetched_at,
        "db_path": str(db_path),
        "snapshot_db_path": str(snapshot_db_path),
        "validation_report": str(report_path),
        "row_metadata_report": str(row_metadata_report_path),
        "chunk_first_report_path": str(chunk_first_report_path),
        "documents": len(documents),
        "chapters": len(chapters),
        "sections": len(sections),
        "statements": len(statements),
        "chunks": len(chunks),
        "mechanisms": len(mechanisms),
        "semantic_models": len(semantic_models),
        "semantic_corpus": len(semantic_corpus),
        "row_mechanism_scores": len(row_mechanism_scores),
        "retrieval_mode": retrieval_mode,
        "retrieval_corpus": retrieval_corpus,
        "ingest_strategy": strategy.strategy_id,
        "ingest_strategy_version": strategy.strategy_version,
        "chunk_target_min_tokens": int(chunk_target_min_tokens),
        "chunk_target_max_tokens": int(chunk_target_max_tokens),
        "chunk_overlap_percent": float(chunk_overlap_percent),
        "pipeline_fingerprint": pipeline_fingerprint,
        "semantic_profile_version": semantic_profile_version,
        "rows_total": counts["applicable"] + counts["not_applicable"],
        "applicable": counts["applicable"],
        "not_applicable": counts["not_applicable"],
        "delta_report_path": str(delta_report_path),
        "dry_run_report_path": str(dry_run_report_path),
        "refresh_contract_path": str(refresh_contract_path),
        "incremental": use_incremental,
        "promotion": promotion,
    }


def parse_args() -> argparse.Namespace:
    return parse_build_args(
        default_extractor_db=DEFAULT_EXTRACTOR_DB,
        default_table_node_id=DEFAULT_TABLE_NODE_ID,
        default_reference_cache_dir=DEFAULT_REFERENCE_CACHE_DIR,
        default_reference_repo_url=DEFAULT_REFERENCE_REPO_URL,
        default_retrieval_mode=DEFAULT_RETRIEVAL_MODE,
        retrieval_corpus_values=RETRIEVAL_CORPUS_VALUES,
        default_retrieval_corpus=DEFAULT_RETRIEVAL_CORPUS,
        default_semantic_profile_version=DEFAULT_SEMANTIC_PROFILE_VERSION,
        default_embedding_model_id=DEFAULT_EMBEDDING_MODEL_ID,
        default_embedding_model_revision=DEFAULT_EMBEDDING_MODEL_REVISION,
        default_embedding_model_license=DEFAULT_EMBEDDING_MODEL_LICENSE,
        default_embedding_dim=DEFAULT_EMBEDDING_DIM,
        default_reranker_model_id=DEFAULT_RERANKER_MODEL_ID,
        default_reranker_model_revision=DEFAULT_RERANKER_MODEL_REVISION,
        default_reranker_model_license=DEFAULT_RERANKER_MODEL_LICENSE,
    )


def run_rust_reference_build(*, args: argparse.Namespace, root: Path) -> dict[str, Any]:
    db_path = (root / args.db_path).resolve()
    snapshot_root = (root / args.snapshot_root).resolve()
    manifest_path = (root / args.manifest_path).resolve()
    report_root = (root / args.report_root).resolve()
    extractor_db = Path(args.extractor_db).expanduser().resolve()
    reference_source_dir = (
        Path(args.reference_source_dir).expanduser().resolve()
        if args.reference_source_dir
        else None
    )
    reference_cache_dir = (root / args.reference_cache_dir).resolve()

    return build_rust_reference_db(
        db_path=db_path,
        snapshot_root=snapshot_root,
        manifest_path=manifest_path,
        extractor_db=extractor_db,
        table_node_id=args.table_node_id,
        reference_source_dir=reference_source_dir,
        reference_cache_dir=reference_cache_dir,
        reference_repo_url=args.reference_repo_url,
        reference_revision=args.reference_revision,
        skip_fetch=args.skip_fetch,
        report_root=report_root,
        min_sections=args.min_sections,
        min_statements=args.min_statements,
        min_mechanisms=args.min_mechanisms,
        retrieval_mode=args.retrieval_mode,
        retrieval_corpus=args.retrieval_corpus,
        semantic_profile_version=args.semantic_profile_version,
        embedding_model_id=args.embedding_model_id,
        embedding_model_revision=args.embedding_model_revision,
        embedding_model_license=args.embedding_model_license,
        embedding_dim=args.embedding_dim,
        reranker_model_id=args.reranker_model_id,
        reranker_model_revision=args.reranker_model_revision,
        reranker_model_license=args.reranker_model_license,
        ingest_strategy=args.ingest_strategy,
        chunk_target_min_tokens=args.chunk_target_min_tokens,
        chunk_target_max_tokens=args.chunk_target_max_tokens,
        chunk_overlap_percent=args.chunk_overlap_percent,
        allow_provenance_mismatch=args.allow_provenance_mismatch,
        incremental=bool(args.incremental),
        force_rebuild=bool(args.force_rebuild),
        staged_output_root=(root / args.staged_output_root).resolve(),
        promotion_root=(root / args.promotion_root).resolve(),
    )


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[3]

    try:
        from retrieval.builders.registry import resolve_builder

        runner = resolve_builder(str(args.corpus))
        summary = runner(args=args, root=root)
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"[build-rust-reference][error] {exc}")
        return EXIT_RUNTIME_FAIL

    print(json.dumps(summary, indent=2, sort_keys=True))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
