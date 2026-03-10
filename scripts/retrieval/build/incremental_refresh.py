from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retrieval.build.reports import validate_chunk_first_db, validate_guidelines_repo_db
from retrieval.corpora.config_loader import load_corpus_runtime_defaults


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class InventoryEntry:
    entry_id: str
    content_sha256: str
    metadata_sha256: str
    parent_id: str = ""
    derived_from_sha256: str = ""
    retrieval_eligible: bool = False


@dataclass(frozen=True)
class DeltaPlan:
    unchanged: tuple[str, ...]
    added: tuple[str, ...]
    updated: tuple[str, ...]
    deleted: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "unchanged": list(self.unchanged),
            "added": list(self.added),
            "updated": list(self.updated),
            "deleted": list(self.deleted),
            "counts": {
                "unchanged": len(self.unchanged),
                "added": len(self.added),
                "updated": len(self.updated),
                "deleted": len(self.deleted),
            },
        }


class IncrementalFallbackRequired(RuntimeError):
    pass


def subtree_invalidation_rules(*, corpus: str) -> dict[str, Any]:
    rules: dict[str, dict[str, Any]] = {
        "core_docs": {
            "root_unit": "document",
            "source_tables": [
                "source_documents",
                "docs",
                "sections",
                "chunks",
                "chunk_spans",
                "core_docs_chunk_metadata",
                "chunks_fts",
                "chunk_fts_rowids",
                "chunk_embeddings",
            ],
            "dependent_tables": [
                "table1_rows",
                "table1_row_profile_terms",
            ],
            "refresh_contract": {
                "delete_scope": "changed_or_deleted_documents",
                "preserve_scope": "unchanged_documents_and_their_embeddings",
                "fts_refresh": ["chunks_fts", "chunk_fts_rowids"],
            },
        },
        "rust_reference": {
            "root_unit": "document",
            "source_tables": [
                "source_documents",
                "docs",
                "sections",
                "statements",
                "statements_fts",
                "chunks",
                "chunk_spans",
                "chunks_fts",
                "chunk_fts_rowids",
                "chunk_embeddings",
            ],
            "dependent_tables": [
                "mechanisms",
                "mechanism_evidence",
                "table1_rows",
                "table1_row_footnotes",
                "table1_row_profile_terms",
                "row_verdicts",
                "row_mechanisms",
                "semantic_models",
                "semantic_corpus",
                "row_mechanism_scores",
                "kb_metadata",
            ],
            "refresh_contract": {
                "delete_scope": "changed_or_deleted_documents",
                "preserve_scope": "unchanged_documents_statements_chunks_and_embeddings",
                "fts_refresh": ["statements_fts", "chunks_fts", "chunk_fts_rowids"],
            },
        },
        "fls_spec": {
            "root_unit": "document",
            "source_tables": [
                "fls_documents",
                "source_documents",
                "docs",
                "fls_sections",
                "sections",
                "paragraphs",
                "fls_paragraph_audit",
                "fls_paragraph_defined_terms",
                "fls_paragraph_term_refs",
                "fls_paragraph_syntax_defs",
                "fls_paragraph_syntax_refs",
                "fls_paragraph_std_refs",
                "fls_paragraph_refs",
                "chunks",
                "chunk_spans",
                "chunks_fts",
                "chunk_fts_rowids",
                "chunk_embeddings",
            ],
            "dependent_tables": ["semantic_models", "paragraphs_fts"],
            "refresh_contract": {
                "delete_scope": "changed_or_deleted_documents",
                "preserve_scope": "unchanged_documents_paragraphs_chunks_and_embeddings",
                "fts_refresh": ["chunks_fts", "chunk_fts_rowids", "paragraphs_fts"],
            },
        },
        "guidelines_repo": {
            "root_unit": "guideline",
            "source_tables": [
                "guideline_records",
                "guideline_blocks",
                "guideline_citations",
                "guideline_bibliography",
                "guideline_bib_links",
                "guideline_exemplars",
                "guideline_inventory",
            ],
            "dependent_tables": [],
            "refresh_contract": {
                "delete_scope": "changed_or_deleted_guidelines",
                "preserve_scope": "unchanged_guidelines",
                "fts_refresh": [],
            },
        },
    }
    return rules[str(corpus)]


def plan_inventory_delta(
    *,
    current: dict[str, InventoryEntry],
    incoming: dict[str, InventoryEntry],
) -> DeltaPlan:
    unchanged: list[str] = []
    added: list[str] = []
    updated: list[str] = []
    deleted: list[str] = []

    current_keys = set(current)
    incoming_keys = set(incoming)
    for entry_id in sorted(current_keys | incoming_keys):
        prior = current.get(entry_id)
        new = incoming.get(entry_id)
        if prior is None and new is not None:
            added.append(entry_id)
        elif prior is not None and new is None:
            deleted.append(entry_id)
        elif prior == new:
            unchanged.append(entry_id)
        else:
            updated.append(entry_id)
    return DeltaPlan(
        unchanged=tuple(unchanged),
        added=tuple(added),
        updated=tuple(updated),
        deleted=tuple(deleted),
    )


def prepare_staged_db(
    *,
    live_db_path: Path,
    staged_root: Path,
    corpus: str,
    run_id: str,
) -> tuple[Path, bool]:
    stage_dir = staged_root / corpus / run_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    staged_db_path = stage_dir / live_db_path.name
    live_exists = live_db_path.exists()
    if live_exists:
        shutil.copy2(live_db_path, staged_db_path)
    return staged_db_path, live_exists


def promote_staged_db(
    *,
    live_db_path: Path,
    staged_db_path: Path,
    promotion_root: Path,
    corpus: str,
    run_id: str,
) -> dict[str, str]:
    promotion_dir = promotion_root / corpus / run_id
    promotion_dir.mkdir(parents=True, exist_ok=True)
    rollback_path = promotion_dir / f"{live_db_path.stem}.rollback{live_db_path.suffix}"
    promoted_copy_path = promotion_dir / f"{live_db_path.stem}.promoted{live_db_path.suffix}"
    live_db_path.parent.mkdir(parents=True, exist_ok=True)

    if live_db_path.exists():
        shutil.copy2(live_db_path, rollback_path)
    shutil.copy2(staged_db_path, promoted_copy_path)
    os.replace(staged_db_path, live_db_path)
    return {
        "rollback_path": str(rollback_path) if rollback_path.exists() else "",
        "promoted_copy_path": str(promoted_copy_path),
        "live_db_path": str(live_db_path),
    }


def snapshot_db_identity(*, corpus: str, db_path: Path) -> dict[str, Any]:
    validation_summary: dict[str, Any] = {}
    inventory_summary: dict[str, Any] = {}
    row_count_summary: dict[str, Any] = {}
    if db_path.exists():
        connection = sqlite3.connect(db_path)
        try:
            inventory_summary = _inventory_summary(connection, corpus=corpus)
            row_count_summary = _row_count_summary(connection, corpus=corpus)
        finally:
            connection.close()
        if corpus in {"fls_spec", "core_docs", "rust_reference"}:
            report = validate_chunk_first_db(db_path, corpus=corpus)
            validation_summary = {
                "passed": bool(report.get("passed", False)),
                "chunk_count": int(report.get("chunk_count", 0) or 0),
                "chunks_fts_count": int(report.get("chunks_fts_count", 0) or 0),
                "chunk_fts_mapping_passed": bool(
                    (report.get("chunk_fts_mapping") or {}).get("passed", False)
                ),
            }
        elif corpus == "guidelines_repo":
            report = validate_guidelines_repo_db(db_path)
            counts = report.get("table_counts") if isinstance(report, dict) else {}
            validation_summary = {
                "passed": bool(report.get("passed", False)),
                "guideline_records": int((counts or {}).get("guideline_records", 0) or 0),
                "guideline_blocks": int((counts or {}).get("guideline_blocks", 0) or 0),
            }
    return {
        "corpus": corpus,
        "db_path": str(db_path.resolve()),
        "db_sha256": _sha256(db_path) if db_path.exists() else "",
        "size_bytes": int(db_path.stat().st_size) if db_path.exists() else 0,
        "inventory_summary": inventory_summary,
        "row_count_summary": row_count_summary,
        "validation_summary": validation_summary,
    }


def capture_cross_db_baseline(*, root: Path) -> dict[str, dict[str, Any]]:
    baseline: dict[str, dict[str, Any]] = {}
    for corpus in ("fls_spec", "core_docs", "rust_reference", "guidelines_repo"):
        defaults = load_corpus_runtime_defaults(root=root, corpus=corpus)
        baseline[corpus] = snapshot_db_identity(corpus=corpus, db_path=defaults.db_path)
    return baseline


def validate_cross_db_non_regression(
    *,
    root: Path,
    report_root: Path,
    run_id: str,
    target_corpus: str,
    baseline: dict[str, dict[str, Any]],
    additional_stage_reports: list[dict[str, Any]] | None = None,
    target_staged_db_path: Path | None = None,
    target_extra_validator: Any | None = None,
) -> Path:
    payload: dict[str, Any] = {
        "target_corpus": target_corpus,
        "checked_at": utc_now(),
        "baseline": baseline,
        "current": {},
        "unchanged_uninvolved": True,
        "failures": [],
        "target_stage_reports": list(additional_stage_reports or []),
        "per_corpus_validation": {},
    }
    validation_stage_root = report_root / "incremental" / run_id / "cross_db_stage"
    for corpus in ("fls_spec", "core_docs", "rust_reference", "guidelines_repo"):
        defaults = load_corpus_runtime_defaults(root=root, corpus=corpus)
        if corpus == target_corpus and target_staged_db_path is not None:
            validation_db_path = target_staged_db_path
        else:
            validation_db_path = _stage_validation_copy(
                source_db_path=defaults.db_path,
                validation_stage_root=validation_stage_root,
                corpus=corpus,
            )
        identity_db_path = defaults.db_path if corpus != target_corpus else validation_db_path
        identity = snapshot_db_identity(corpus=corpus, db_path=identity_db_path)
        payload["current"][corpus] = identity
        per_corpus_reports = validate_staged_corpus(
            corpus=corpus,
            staged_db_path=validation_db_path,
            extra_validator=target_extra_validator if corpus == target_corpus else None,
        )
        payload["per_corpus_validation"][corpus] = {
            "db_path": str(validation_db_path),
            "reports": per_corpus_reports,
        }
        if corpus != target_corpus and identity != baseline.get(corpus, {}):
            payload["unchanged_uninvolved"] = False
            payload["failures"].append(f"uninvolved_corpus_changed::{corpus}")
    path = report_root / "incremental" / run_id / f"{target_corpus}_cross_db_validation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if payload["failures"]:
        raise RuntimeError("cross-db validation failed: " + ", ".join(payload["failures"]))
    return path


def write_promotion_provenance(
    *,
    report_root: Path,
    corpus: str,
    run_id: str,
    payload: dict[str, Any],
) -> Path:
    path = report_root / "incremental" / run_id / f"{corpus}_promotion_provenance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_operator_summary(
    *,
    report_root: Path,
    corpus: str,
    run_id: str,
    payload: dict[str, Any],
) -> Path:
    path = report_root / "incremental" / run_id / f"{corpus}_operator_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_refresh_contract_report(
    *,
    report_root: Path,
    corpus: str,
    run_id: str,
) -> Path:
    path = report_root / "incremental" / run_id / f"{corpus}_refresh_contract.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(subtree_invalidation_rules(corpus=corpus), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def require_force_rebuild(*, corpus: str, reason: str, force_rebuild: bool) -> None:
    if not force_rebuild:
        raise IncrementalFallbackRequired(
            f"incremental_fallback_requires_force_rebuild::{corpus}::{reason}"
        )


def validate_staged_corpus(
    *,
    corpus: str,
    staged_db_path: Path,
    extra_validator: Any | None = None,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    if corpus in {"fls_spec", "core_docs", "rust_reference"}:
        chunk_first = validate_chunk_first_db(staged_db_path, corpus=corpus)
        reports.append({"kind": "chunk_first", "report": chunk_first})
        if not chunk_first.get("passed", False):
            raise RuntimeError(f"staged validation failed::{corpus}::chunk_first")
    if corpus == "guidelines_repo":
        guidelines_report = validate_guidelines_repo_db(staged_db_path)
        reports.append({"kind": "guidelines_repo", "report": guidelines_report})
        if not guidelines_report.get("passed", False):
            raise RuntimeError("staged validation failed::guidelines_repo")
    if extra_validator is not None:
        extra_report = extra_validator(staged_db_path)
        reports.append({"kind": "extra", "report": extra_report})
        if not bool(extra_report.get("passed", False)):
            raise RuntimeError(f"staged validation failed::{corpus}::extra")
    audit_report = validate_incremental_audits(staged_db_path=staged_db_path, corpus=corpus)
    reports.append({"kind": "incremental_audits", "report": audit_report})
    if not bool(audit_report.get("passed", False)):
        raise RuntimeError(f"staged validation failed::{corpus}::incremental_audits")
    return reports


def validate_incremental_audits(*, staged_db_path: Path, corpus: str) -> dict[str, Any]:
    connection = sqlite3.connect(staged_db_path)
    failures: list[str] = []
    try:
        materialization_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM materialization_deltas WHERE corpus = ?",
                (corpus,),
            ).fetchone()[0]
        )
        embedding_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM embedding_reuse_audit WHERE corpus = ?",
                (corpus,),
            ).fetchone()[0]
        )
        provenance_kind = "snapshot"
        if _table_exists(connection, "pipeline_runs"):
            provenance_kind = "pipeline_run"
            provenance_rows = int(
                connection.execute(
                    "SELECT COUNT(*) FROM pipeline_runs WHERE corpus = ?",
                    (corpus,),
                ).fetchone()[0]
            )
        else:
            provenance_rows = int(
                connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            )
    finally:
        connection.close()
    if materialization_rows <= 0:
        failures.append("missing_materialization_delta")
    if embedding_rows <= 0:
        failures.append("missing_embedding_reuse_audit")
    if provenance_rows <= 0:
        failures.append(f"missing_provenance::{provenance_kind}")
    return {
        "corpus": corpus,
        "passed": not failures,
        "materialization_delta_rows": materialization_rows,
        "embedding_reuse_audit_rows": embedding_rows,
        "provenance_kind": provenance_kind,
        "provenance_rows": provenance_rows,
        "failures": failures,
    }


def write_delta_report(
    *,
    report_root: Path,
    corpus: str,
    run_id: str,
    payload: dict[str, Any],
    phase: str = "post_apply",
) -> Path:
    path = report_root / "incremental" / run_id / f"{corpus}_{phase}_delta_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def estimate_embedding_impact(
    *,
    planned_delta: DeltaPlan,
    unchanged_chunk_ids: list[str] | tuple[str, ...],
    changed_chunk_ids: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    return {
        "expected_reused_embedding_units": len(list(unchanged_chunk_ids)),
        "expected_recomputed_embedding_units": len(list(changed_chunk_ids)),
        "changed_roots": {
            "added": len(planned_delta.added),
            "updated": len(planned_delta.updated),
            "deleted": len(planned_delta.deleted),
            "unchanged": len(planned_delta.unchanged),
        },
    }


def load_source_inventory_documents(
    connection: sqlite3.Connection,
    *,
    corpus: str,
) -> dict[str, InventoryEntry]:
    if not _table_exists(connection, "source_inventory_documents"):
        return {}
    rows = connection.execute(
        """
        SELECT document_id, content_sha256, metadata_sha256
        FROM source_inventory_documents
        WHERE corpus = ?
        """,
        (corpus,),
    ).fetchall()
    return {
        str(row[0]): InventoryEntry(
            entry_id=str(row[0]),
            content_sha256=str(row[1]),
            metadata_sha256=str(row[2]),
        )
        for row in rows
    }


def ensure_incremental_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS source_inventory_documents (
            corpus TEXT NOT NULL,
            document_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL DEFAULT '',
            source_path TEXT NOT NULL DEFAULT '',
            content_sha256 TEXT NOT NULL DEFAULT '',
            metadata_sha256 TEXT NOT NULL DEFAULT '',
            last_materialized_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (corpus, document_id)
        );

        CREATE TABLE IF NOT EXISTS source_inventory_sections (
            corpus TEXT NOT NULL,
            section_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            content_sha256 TEXT NOT NULL DEFAULT '',
            metadata_sha256 TEXT NOT NULL DEFAULT '',
            last_materialized_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (corpus, section_id)
        );

        CREATE TABLE IF NOT EXISTS source_inventory_units (
            corpus TEXT NOT NULL,
            unit_kind TEXT NOT NULL,
            unit_id TEXT NOT NULL,
            parent_id TEXT NOT NULL DEFAULT '',
            content_sha256 TEXT NOT NULL DEFAULT '',
            metadata_sha256 TEXT NOT NULL DEFAULT '',
            derived_from_sha256 TEXT NOT NULL DEFAULT '',
            retrieval_eligible INTEGER NOT NULL DEFAULT 0,
            last_materialized_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (corpus, unit_kind, unit_id)
        );

        CREATE TABLE IF NOT EXISTS guideline_inventory (
            guideline_id TEXT PRIMARY KEY,
            source_file_path TEXT NOT NULL DEFAULT '',
            source_hash TEXT NOT NULL DEFAULT '',
            metadata_hash TEXT NOT NULL DEFAULT '',
            blocks_hash TEXT NOT NULL DEFAULT '',
            citations_hash TEXT NOT NULL DEFAULT '',
            bibliography_hash TEXT NOT NULL DEFAULT '',
            last_ingested_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS materialization_deltas (
            run_id TEXT PRIMARY KEY,
            corpus TEXT NOT NULL,
            mode TEXT NOT NULL,
            base_snapshot_id TEXT NOT NULL DEFAULT '',
            target_snapshot_id TEXT NOT NULL DEFAULT '',
            delta_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS embedding_reuse_audit (
            run_id TEXT NOT NULL,
            corpus TEXT NOT NULL,
            model_fingerprint TEXT NOT NULL DEFAULT '',
            reused_count INTEGER NOT NULL DEFAULT 0,
            recomputed_count INTEGER NOT NULL DEFAULT 0,
            audited_at TEXT NOT NULL,
            PRIMARY KEY (run_id, corpus, model_fingerprint)
        );
        """
    )


def replace_source_inventory(
    connection: sqlite3.Connection,
    *,
    corpus: str,
    snapshot_id: str,
    documents: dict[str, InventoryEntry],
    sections: dict[str, InventoryEntry],
    units: dict[tuple[str, str], InventoryEntry],
    materialized_at: str,
) -> None:
    connection.execute("DELETE FROM source_inventory_documents WHERE corpus = ?", (corpus,))
    connection.execute("DELETE FROM source_inventory_sections WHERE corpus = ?", (corpus,))
    connection.execute("DELETE FROM source_inventory_units WHERE corpus = ?", (corpus,))

    for entry in documents.values():
        connection.execute(
            """
            INSERT INTO source_inventory_documents(
                corpus, document_id, snapshot_id, source_path, content_sha256,
                metadata_sha256, last_materialized_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corpus,
                entry.entry_id,
                snapshot_id,
                entry.parent_id,
                entry.content_sha256,
                entry.metadata_sha256,
                materialized_at,
            ),
        )

    for entry in sections.values():
        connection.execute(
            """
            INSERT INTO source_inventory_sections(
                corpus, section_id, document_id, content_sha256,
                metadata_sha256, last_materialized_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                corpus,
                entry.entry_id,
                entry.parent_id,
                entry.content_sha256,
                entry.metadata_sha256,
                materialized_at,
            ),
        )

    for (unit_kind, unit_id), entry in units.items():
        connection.execute(
            """
            INSERT INTO source_inventory_units(
                corpus, unit_kind, unit_id, parent_id, content_sha256,
                metadata_sha256, derived_from_sha256, retrieval_eligible,
                last_materialized_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                corpus,
                unit_kind,
                unit_id,
                entry.parent_id,
                entry.content_sha256,
                entry.metadata_sha256,
                entry.derived_from_sha256,
                1 if entry.retrieval_eligible else 0,
                materialized_at,
            ),
        )


def replace_guideline_inventory(
    connection: sqlite3.Connection,
    *,
    inventory_rows: list[dict[str, str]],
) -> None:
    connection.execute("DELETE FROM guideline_inventory")
    for row in inventory_rows:
        connection.execute(
            """
            INSERT INTO guideline_inventory(
                guideline_id,
                source_file_path,
                source_hash,
                metadata_hash,
                blocks_hash,
                citations_hash,
                bibliography_hash,
                last_ingested_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["guideline_id"],
                row["source_file_path"],
                row["source_hash"],
                row["metadata_hash"],
                row["blocks_hash"],
                row["citations_hash"],
                row["bibliography_hash"],
                row["last_ingested_at"],
            ),
        )


def load_guideline_inventory(connection: sqlite3.Connection) -> dict[str, InventoryEntry]:
    if not _table_exists(connection, "guideline_inventory"):
        return {}
    rows = connection.execute(
        """
        SELECT guideline_id, source_hash, metadata_hash, source_file_path
        FROM guideline_inventory
        """
    ).fetchall()
    return {
        str(row[0]): InventoryEntry(
            entry_id=str(row[0]),
            content_sha256=str(row[1]),
            metadata_sha256=str(row[2]),
            parent_id=str(row[3]),
        )
        for row in rows
    }


def record_materialization_delta(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    corpus: str,
    mode: str,
    base_snapshot_id: str,
    target_snapshot_id: str,
    delta_payload: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO materialization_deltas(
            run_id, corpus, mode, base_snapshot_id, target_snapshot_id, delta_json, created_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            corpus,
            mode,
            base_snapshot_id,
            target_snapshot_id,
            json.dumps(delta_payload, sort_keys=True),
            utc_now(),
        ),
    )


def record_embedding_reuse_audit(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    corpus: str,
    model_fingerprint: str,
    reused_count: int,
    recomputed_count: int,
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO embedding_reuse_audit(
            run_id, corpus, model_fingerprint, reused_count, recomputed_count, audited_at
        ) VALUES(?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            corpus,
            model_fingerprint,
            int(reused_count),
            int(recomputed_count),
            utc_now(),
        ),
    )


def embedding_reuse_key(
    *, stable_id: str, content_sha256: str, model_id: str, embed_version: str
) -> str:
    return _hash_json(
        {
            "stable_id": stable_id,
            "content_sha256": content_sha256,
            "model_id": model_id,
            "embed_version": embed_version,
        }
    )


def audit_preserved_chunk_embeddings(
    connection: sqlite3.Connection,
    *,
    corpus: str,
    unchanged_chunk_ids: list[str],
) -> dict[str, Any]:
    if not unchanged_chunk_ids or not _table_exists(connection, "chunk_embeddings"):
        return {
            "corpus": corpus,
            "reused_count": 0,
            "recomputed_count": 0,
            "reused_keys": [],
        }
    placeholders = ", ".join("?" for _ in unchanged_chunk_ids)
    rows = connection.execute(
        (
            "SELECT chunk_uid, model_id, embed_version, text_sha256 "
            f"FROM chunk_embeddings WHERE chunk_uid IN ({placeholders}) "
            "ORDER BY chunk_uid, model_id, embed_version"
        ),
        tuple(unchanged_chunk_ids),
    ).fetchall()
    reused_keys = [
        embedding_reuse_key(
            stable_id=str(row[0]),
            content_sha256=str(row[3]),
            model_id=str(row[1]),
            embed_version=str(row[2]),
        )
        for row in rows
    ]
    return {
        "corpus": corpus,
        "reused_count": len(rows),
        "recomputed_count": 0,
        "reused_keys": reused_keys,
    }


def build_reference_inventory(
    *,
    documents: list[Any],
    sections: list[Any],
    statements: list[Any],
    chunks: list[Any],
) -> tuple[
    dict[str, InventoryEntry], dict[str, InventoryEntry], dict[tuple[str, str], InventoryEntry]
]:
    document_inventory = {
        str(document.document_id): InventoryEntry(
            entry_id=str(document.document_id),
            content_sha256=str(document.source_sha256),
            metadata_sha256=_hash_json(
                {
                    "rel_path": str(document.rel_path),
                    "title": str(document.title),
                    "chapter_id": str(document.chapter_id),
                    "doc_order": int(document.doc_order),
                }
            ),
            parent_id=str(document.rel_path),
        )
        for document in documents
    }
    section_inventory = {
        str(section.section_id): InventoryEntry(
            entry_id=str(section.section_id),
            content_sha256=str(section.source_sha256),
            metadata_sha256=_hash_json(
                {
                    "document_id": str(section.document_id),
                    "heading": str(section.heading),
                    "anchor": str(section.anchor),
                    "order_index": int(section.order_index),
                    "level": int(section.level),
                }
            ),
            parent_id=str(section.document_id),
        )
        for section in sections
    }
    unit_inventory: dict[tuple[str, str], InventoryEntry] = {}
    for statement in statements:
        unit_inventory[("statement", str(statement.statement_id))] = InventoryEntry(
            entry_id=str(statement.statement_id),
            content_sha256=str(statement.source_sha256),
            metadata_sha256=_hash_json(
                {
                    "section_id": str(statement.section_id),
                    "statement_type": str(statement.statement_type),
                    "sentence_index": int(statement.sentence_index),
                }
            ),
            parent_id=str(statement.section_id),
            derived_from_sha256=str(statement.source_sha256),
            retrieval_eligible=True,
        )
    for chunk in chunks:
        unit_inventory[("chunk", str(chunk.chunk_uid))] = InventoryEntry(
            entry_id=str(chunk.chunk_uid),
            content_sha256=str(chunk.source_sha256),
            metadata_sha256=_hash_json(
                {
                    "section_id": str(chunk.section_id),
                    "order_index": int(chunk.order_index),
                    "token_len": int(chunk.token_len),
                }
            ),
            parent_id=str(chunk.section_id),
            derived_from_sha256=str(chunk.source_sha256),
            retrieval_eligible=True,
        )
    return document_inventory, section_inventory, unit_inventory


def build_fls_inventory(
    *,
    paragraphs: list[Any],
) -> tuple[
    dict[str, InventoryEntry], dict[str, InventoryEntry], dict[tuple[str, str], InventoryEntry]
]:
    document_map: dict[str, list[Any]] = {}
    section_map: dict[str, list[Any]] = {}
    unit_inventory: dict[tuple[str, str], InventoryEntry] = {}
    for paragraph in paragraphs:
        document_map.setdefault(str(paragraph.document_link), []).append(paragraph)
        section_map.setdefault(str(paragraph.section_link), []).append(paragraph)
        unit_inventory[("paragraph", str(paragraph.paragraph_id))] = InventoryEntry(
            entry_id=str(paragraph.paragraph_id),
            content_sha256=str(paragraph.checksum),
            metadata_sha256=_hash_json(
                {
                    "section_id": str(paragraph.section_id),
                    "paragraph_number": str(paragraph.paragraph_number),
                    "source_file": str(paragraph.source_file),
                }
            ),
            parent_id=str(paragraph.section_link),
            derived_from_sha256=str(paragraph.checksum),
            retrieval_eligible=True,
        )
        unit_inventory[("chunk", str(paragraph.paragraph_id))] = InventoryEntry(
            entry_id=str(paragraph.paragraph_id),
            content_sha256=str(paragraph.checksum),
            metadata_sha256=_hash_json(
                {
                    "section_id": str(paragraph.section_id),
                    "document_link": str(paragraph.document_link),
                    "retrieval_eligible": True,
                }
            ),
            parent_id=str(paragraph.section_id),
            derived_from_sha256=str(paragraph.checksum),
            retrieval_eligible=True,
        )
    document_inventory = {
        document_link: InventoryEntry(
            entry_id=document_link,
            content_sha256=_hash_json(
                [
                    str(paragraph.checksum)
                    for paragraph in sorted(rows, key=lambda row: row.paragraph_id)
                ]
            ),
            metadata_sha256=_hash_json(
                {
                    "document_link": document_link,
                    "source_files": sorted({str(row.source_file) for row in rows}),
                }
            ),
            parent_id=document_link,
        )
        for document_link, rows in document_map.items()
    }
    section_inventory = {
        section_link: InventoryEntry(
            entry_id=section_link,
            content_sha256=_hash_json(
                [
                    str(paragraph.checksum)
                    for paragraph in sorted(rows, key=lambda row: row.paragraph_id)
                ]
            ),
            metadata_sha256=_hash_json(
                {
                    "section_link": section_link,
                    "section_id": str(rows[0].section_id) if rows else "",
                    "document_link": str(rows[0].document_link) if rows else "",
                }
            ),
            parent_id=str(rows[0].document_link) if rows else "",
        )
        for section_link, rows in section_map.items()
    }
    return document_inventory, section_inventory, unit_inventory


def _hash_json(payload: Any) -> str:
    return (
        __import__("hashlib")
        .sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        )
        .hexdigest()
    )


def _inventory_summary(connection: sqlite3.Connection, *, corpus: str) -> dict[str, Any]:
    if not _table_exists(connection, "source_inventory_documents"):
        return {}
    rows = connection.execute(
        (
            "SELECT document_id, content_sha256, metadata_sha256 "
            "FROM source_inventory_documents WHERE corpus = ? ORDER BY document_id"
        ),
        (corpus,),
    ).fetchall()
    payload = [[str(row[0]), str(row[1]), str(row[2])] for row in rows]
    return {
        "document_count": len(rows),
        "key_hash": _hash_json(payload),
    }


def _row_count_summary(connection: sqlite3.Connection, *, corpus: str) -> dict[str, Any]:
    table_sets = {
        "fls_spec": [
            "source_documents",
            "sections",
            "paragraphs",
            "chunks",
            "chunks_fts",
            "chunk_fts_rowids",
        ],
        "core_docs": [
            "source_documents",
            "sections",
            "chunks",
            "chunks_fts",
            "chunk_fts_rowids",
            "core_docs_chunk_metadata",
        ],
        "rust_reference": [
            "source_documents",
            "sections",
            "statements",
            "chunks",
            "semantic_corpus",
            "row_mechanism_scores",
        ],
        "guidelines_repo": [
            "guideline_records",
            "guideline_blocks",
            "guideline_citations",
            "guideline_bibliography",
            "guideline_bib_links",
            "guideline_exemplars",
        ],
    }
    counts: dict[str, int] = {}
    for table_name in table_sets.get(corpus, []):
        if _table_exists(connection, table_name):
            counts[table_name] = int(
                connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            )
    return counts


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _stage_validation_copy(
    *, source_db_path: Path, validation_stage_root: Path, corpus: str
) -> Path:
    validation_stage_root.mkdir(parents=True, exist_ok=True)
    staged_path = validation_stage_root / f"{corpus}.sqlite"
    shutil.copy2(source_db_path, staged_path)
    return staged_path
