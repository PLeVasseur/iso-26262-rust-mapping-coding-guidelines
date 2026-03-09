from __future__ import annotations

import shutil
import sqlite3
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path

from retrieval.core_docs.rustdoc_extract import (
    CANONICAL_TARGET,
    OVERLAY_ITEM_CAP,
    TARGET_MATRIX,
    ParsedItem,
    TargetCfg,
    generate_rustdoc_json as _generate_rustdoc_json,
    load_parsed_items as _load_parsed_items,
    sha256_text as _sha256_text,
    split_chunks as _split_chunks,
    target_cfg as _target_cfg,
    token_len as _token_len,
    toolchain_version as _toolchain_version,
    utc_now as _utc_now,
    write_manifest as _write_manifest,
)

from retrieval.core.provenance import (
    apply_pending_migrations,
    canonical_json_hash,
    compute_source_state_from_db,
    record_pipeline_run,
)
from retrieval.build.chunk_fts_validation import (
    enforce_chunk_fts_mapping,
    refresh_chunk_fts_rowids,
)
from retrieval.build.reports import (
    validate_chunk_first_db,
    write_chunk_first_validation_report,
    write_current_chunk_first_validation_report,
)
from retrieval.build.schema import initialize_schema
from retrieval.build.table1_rows import resolve_table1_rows as _resolve_table1_rows
from retrieval.ingest.contracts import CleanInput
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

    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db_path)
    try:
        initialize_schema(connection)
        connection.commit()
    finally:
        connection.close()

    latest_migration_id, _ = apply_pending_migrations(db_path, root=root)
    strategy = resolve_ingest_strategy(
        str(getattr(args, "ingest_strategy", "core_docs_rustdoc_v1"))
    )

    connection = sqlite3.connect(db_path)
    try:
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

        _insert_table1_rows(connection, table_rows)

        doc_seen: dict[str, tuple[str, int]] = {}
        section_order = 0
        chunk_order = 0
        for parsed in all_items:
            path_parts = parsed.item_path.split("::")
            module_path = "::".join(path_parts[: min(3, len(path_parts))])
            doc_uid = f"{parsed.target.target_triple}::{module_path}"
            if doc_uid not in doc_seen:
                doc_seen[doc_uid] = (parsed.target.target_triple, len(doc_seen) + 1)
                source_path = (
                    parsed.target.target_triple + "/" + module_path.replace("::", "/") + ".md"
                )
                source_sha = _sha256_text(source_path)
                connection.execute(
                    """
                    INSERT INTO source_documents(
                        document_id,
                        snapshot_id,
                        chapter_id,
                        rel_path,
                        title,
                        source_sha256,
                        source_fetched_at,
                        source_commit_sha,
                        order_index
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_uid,
                        snapshot_id,
                        "chapter:core-docs",
                        source_path,
                        module_path,
                        source_sha,
                        fetched_at,
                        source_revision,
                        doc_seen[doc_uid][1],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO docs(
                        doc_uid,
                        source_path,
                        title,
                        revision,
                        fetched_at,
                        source_sha256,
                        chapter_id,
                        order_index
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_uid,
                        source_path,
                        module_path,
                        source_revision,
                        fetched_at,
                        source_sha,
                        "chapter:core-docs",
                        doc_seen[doc_uid][1],
                    ),
                )

            header = (
                f"Item: {parsed.item_path}\n"
                f"Kind: {parsed.item_kind}\n"
                f"Signature: {parsed.signature}\n"
                f"Stability: {parsed.stability}\n"
                f"Target: {parsed.target.target_triple}\n"
            )
            body = "\n\n".join(
                segment
                for segment in (
                    parsed.docs_text,
                    f"Safety\n{parsed.safety_notes}" if parsed.safety_notes else "",
                    f"Panics\n{parsed.panic_behavior}" if parsed.panic_behavior else "",
                    f"Examples\n{parsed.example_snippets}" if parsed.example_snippets else "",
                )
                if segment.strip()
            )
            raw_text = f"{header}\n{body}".strip()
            split_chunks = _split_chunks(
                raw_text,
                min_tokens=int(getattr(args, "chunk_target_min_tokens", 150)),
                target_tokens=260,
                max_tokens=int(getattr(args, "chunk_target_max_tokens", 500)),
            )

            for local_idx, raw_chunk in enumerate(split_chunks, start=1):
                section_order += 1
                section_seed = (
                    parsed.item_id
                    + "::"
                    + parsed.item_path
                    + "::"
                    + parsed.signature
                    + "::"
                    + str(local_idx)
                    + "::"
                    + parsed.target.target_triple
                    + "::"
                    + parsed.source_anchor
                )
                section_id = f"section::{_sha256_text(section_seed)}"
                source_sha = _sha256_text(raw_chunk)
                connection.execute(
                    """
                    INSERT INTO sections(
                        section_id,
                        snapshot_id,
                        document_id,
                        chapter_id,
                        anchor,
                        heading,
                        order_index,
                        level,
                        text,
                        source_sha256,
                        source_fetched_at,
                        source_commit_sha
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        section_id,
                        snapshot_id,
                        doc_uid,
                        "chapter:core-docs",
                        f"item-{_sha256_text(parsed.item_path)[:12]}",
                        parsed.item_path,
                        section_order,
                        2,
                        raw_chunk,
                        source_sha,
                        fetched_at,
                        source_revision,
                    ),
                )

                clean_result = strategy.clean_text(
                    CleanInput(
                        raw_text=raw_chunk,
                        source_type="rustdoc_item",
                        context={
                            "item_path": parsed.item_path,
                            "target_triple": parsed.target.target_triple,
                            "source_anchor": parsed.source_anchor,
                        },
                    )
                )
                chunk_payload = (
                    f"{clean_result.cleaned_text}\n"
                    f"target_triple={parsed.target.target_triple}\n"
                    f"target_os={parsed.target.target_os}\n"
                    f"target_arch={parsed.target.target_arch}\n"
                    f"target_env={parsed.target.target_env}"
                ).strip()
                chunk_order += 1
                chunk_seed = section_id + "::" + str(local_idx) + "::" + chunk_payload
                chunk_uid = f"chunk::{_sha256_text(chunk_seed)}"
                connection.execute(
                    """
                    INSERT INTO chunks(
                        chunk_uid,
                        section_id,
                        raw_text,
                        clean_text,
                        char_len,
                        token_len,
                        source_sha256,
                        source_fetched_at,
                        source_commit_sha,
                        order_index
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_uid,
                        section_id,
                        raw_chunk,
                        chunk_payload,
                        len(chunk_payload),
                        _token_len(chunk_payload),
                        source_sha,
                        fetched_at,
                        source_revision,
                        chunk_order,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO core_docs_chunk_metadata(
                        chunk_uid,
                        item_path,
                        item_kind,
                        signature,
                        stability,
                        safety_notes,
                        panic_behavior,
                        example_snippets,
                        target_triple,
                        target_os,
                        target_arch,
                        target_env,
                        cfg_signature,
                        cfg_signature_sha256
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_uid,
                        parsed.item_path,
                        parsed.item_kind,
                        parsed.signature,
                        parsed.stability,
                        parsed.safety_notes,
                        parsed.panic_behavior,
                        parsed.example_snippets,
                        parsed.target.target_triple,
                        parsed.target.target_os,
                        parsed.target.target_arch,
                        parsed.target.target_env,
                        parsed.target.cfg_signature,
                        parsed.target.cfg_signature_sha256,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO chunk_spans(
                        chunk_uid,
                        source_anchor,
                        start_offset,
                        end_offset,
                        span_order
                    )
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (chunk_uid, parsed.source_anchor, 0, len(chunk_payload), 1),
                )

        connection.execute("DELETE FROM chunks_fts")
        connection.execute(
            """
            INSERT INTO chunks_fts(chunk_uid, section_id, section_heading, chunk_text)
            SELECT c.chunk_uid, c.section_id, COALESCE(s.heading, ''), c.clean_text
            FROM chunks AS c
            LEFT JOIN sections AS s ON s.section_id = c.section_id
            ORDER BY c.chunk_uid ASC
            """
        )
        refresh_chunk_fts_rowids(connection)
        enforce_chunk_fts_mapping(connection, context="core_docs build")
        connection.commit()
    finally:
        connection.close()

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
        "targets": list(TARGET_MATRIX),
        "items": len(all_items),
        "rows": len(table_rows),
        "pipeline_fingerprint": fingerprint,
    }
