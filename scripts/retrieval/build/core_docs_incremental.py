from __future__ import annotations

import json
import sqlite3
from typing import Any

from retrieval.build.chunk_fts_validation import enforce_chunk_fts_mapping, refresh_chunk_fts_rowids
from retrieval.build.incremental_refresh import InventoryEntry
from retrieval.core_docs.rustdoc_extract import ParsedItem
from retrieval.core_docs.rustdoc_extract import sha256_text as _sha256_text
from retrieval.ingest.contracts import CleanInput, IngestStrategy


def _token_len(text: str) -> int:
    return len([token for token in text.replace("\n", " ").split(" ") if token.strip()])


def build_core_docs_materialized_view(
    *,
    all_items: list[ParsedItem],
    strategy: IngestStrategy,
    snapshot_id: str,
    fetched_at: str,
    source_revision: str,
    chunk_target_min_tokens: int,
    chunk_target_max_tokens: int,
    split_chunks: Any,
) -> dict[str, Any]:
    doc_seen: dict[str, tuple[str, int]] = {}
    docs: dict[str, dict[str, Any]] = {}
    document_inventory: dict[str, InventoryEntry] = {}
    section_inventory: dict[str, InventoryEntry] = {}
    unit_inventory: dict[tuple[str, str], InventoryEntry] = {}
    section_order = 0
    chunk_order = 0

    for parsed in all_items:
        path_parts = parsed.item_path.split("::")
        module_path = "::".join(path_parts[: min(3, len(path_parts))])
        doc_uid = f"{parsed.target.target_triple}::{module_path}"
        if doc_uid not in doc_seen:
            doc_seen[doc_uid] = (parsed.target.target_triple, len(doc_seen) + 1)
            source_path = parsed.target.target_triple + "/" + module_path.replace("::", "/") + ".md"
            docs[doc_uid] = {
                "document_id": doc_uid,
                "source_path": source_path,
                "title": module_path,
                "source_sha256": _sha256_text(source_path),
                "order_index": doc_seen[doc_uid][1],
                "sections": [],
            }

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
        raw_chunks = split_chunks(
            raw_text,
            min_tokens=int(chunk_target_min_tokens),
            target_tokens=260,
            max_tokens=int(chunk_target_max_tokens),
        )
        for local_idx, raw_chunk in enumerate(raw_chunks, start=1):
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

            section_payload = {
                "section_id": section_id,
                "snapshot_id": snapshot_id,
                "document_id": doc_uid,
                "chapter_id": "chapter:core-docs",
                "anchor": f"item-{_sha256_text(parsed.item_path)[:12]}",
                "heading": parsed.item_path,
                "order_index": section_order,
                "level": 2,
                "text": raw_chunk,
                "source_sha256": source_sha,
                "source_fetched_at": fetched_at,
                "source_commit_sha": source_revision,
                "chunk": {
                    "chunk_uid": chunk_uid,
                    "section_id": section_id,
                    "raw_text": raw_chunk,
                    "clean_text": chunk_payload,
                    "char_len": len(chunk_payload),
                    "token_len": _token_len(chunk_payload),
                    "source_sha256": source_sha,
                    "source_fetched_at": fetched_at,
                    "source_commit_sha": source_revision,
                    "order_index": chunk_order,
                    "metadata": {
                        "item_path": parsed.item_path,
                        "item_kind": parsed.item_kind,
                        "signature": parsed.signature,
                        "stability": parsed.stability,
                        "safety_notes": parsed.safety_notes,
                        "panic_behavior": parsed.panic_behavior,
                        "example_snippets": parsed.example_snippets,
                        "target_triple": parsed.target.target_triple,
                        "target_os": parsed.target.target_os,
                        "target_arch": parsed.target.target_arch,
                        "target_env": parsed.target.target_env,
                        "cfg_signature": parsed.target.cfg_signature,
                        "cfg_signature_sha256": parsed.target.cfg_signature_sha256,
                    },
                    "span": {
                        "source_anchor": parsed.source_anchor,
                        "start_offset": 0,
                        "end_offset": len(chunk_payload),
                        "span_order": 1,
                    },
                },
            }
            docs[doc_uid]["sections"].append(section_payload)

    for doc_uid, payload in docs.items():
        content_hash = _sha256_text("\n\n".join(section["text"] for section in payload["sections"]))
        metadata_hash = _sha256_text(
            json.dumps(
                {
                    "document_id": doc_uid,
                    "source_path": payload["source_path"],
                    "title": payload["title"],
                    "order_index": payload["order_index"],
                },
                sort_keys=True,
            )
        )
        document_inventory[doc_uid] = InventoryEntry(
            entry_id=doc_uid,
            content_sha256=content_hash,
            metadata_sha256=metadata_hash,
            parent_id=payload["source_path"],
        )
        for section in payload["sections"]:
            section_inventory[section["section_id"]] = InventoryEntry(
                entry_id=section["section_id"],
                content_sha256=section["source_sha256"],
                metadata_sha256=_sha256_text(
                    json.dumps(
                        {
                            "heading": section["heading"],
                            "order_index": section["order_index"],
                            "document_id": doc_uid,
                        },
                        sort_keys=True,
                    )
                ),
                parent_id=doc_uid,
            )
            chunk = section["chunk"]
            unit_inventory[("chunk", chunk["chunk_uid"])] = InventoryEntry(
                entry_id=chunk["chunk_uid"],
                content_sha256=chunk["source_sha256"],
                metadata_sha256=_sha256_text(
                    json.dumps(
                        {
                            "order_index": chunk["order_index"],
                            "section_id": section["section_id"],
                            "clean_text": chunk["clean_text"],
                        },
                        sort_keys=True,
                    )
                ),
                parent_id=section["section_id"],
                derived_from_sha256=section["source_sha256"],
                retrieval_eligible=True,
            )

    return {
        "docs": docs,
        "document_inventory": document_inventory,
        "section_inventory": section_inventory,
        "unit_inventory": unit_inventory,
    }


def delete_core_docs_documents(connection: sqlite3.Connection, document_ids: list[str]) -> None:
    if not document_ids:
        return
    placeholders = ", ".join("?" for _ in document_ids)
    chunk_rows = connection.execute(
        f"""
        SELECT c.chunk_uid
        FROM chunks AS c
        JOIN sections AS s ON s.section_id = c.section_id
        WHERE s.document_id IN ({placeholders})
        """,
        tuple(document_ids),
    ).fetchall()
    chunk_ids = [str(row[0]) for row in chunk_rows]
    section_rows = connection.execute(
        f"SELECT section_id FROM sections WHERE document_id IN ({placeholders})",
        tuple(document_ids),
    ).fetchall()
    section_ids = [str(row[0]) for row in section_rows]
    if chunk_ids:
        chunk_placeholders = ", ".join("?" for _ in chunk_ids)
        connection.execute(
            f"DELETE FROM chunk_embeddings WHERE chunk_uid IN ({chunk_placeholders})",
            tuple(chunk_ids),
        )
        connection.execute(
            f"DELETE FROM core_docs_chunk_metadata WHERE chunk_uid IN ({chunk_placeholders})",
            tuple(chunk_ids),
        )
        connection.execute(
            f"DELETE FROM chunk_spans WHERE chunk_uid IN ({chunk_placeholders})",
            tuple(chunk_ids),
        )
        connection.execute(
            f"DELETE FROM chunks WHERE chunk_uid IN ({chunk_placeholders})",
            tuple(chunk_ids),
        )
    if section_ids:
        section_placeholders = ", ".join("?" for _ in section_ids)
        connection.execute(
            f"DELETE FROM sections WHERE section_id IN ({section_placeholders})",
            tuple(section_ids),
        )
    connection.execute(
        f"DELETE FROM source_documents WHERE document_id IN ({placeholders})",
        tuple(document_ids),
    )
    connection.execute(f"DELETE FROM docs WHERE doc_uid IN ({placeholders})", tuple(document_ids))


def upsert_core_docs_document(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    fetched_at: str,
    source_revision: str,
    document: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO source_documents(
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
            document["document_id"],
            snapshot_id,
            "chapter:core-docs",
            document["source_path"],
            document["title"],
            document["source_sha256"],
            fetched_at,
            source_revision,
            document["order_index"],
        ),
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO docs(
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
            document["document_id"],
            document["source_path"],
            document["title"],
            source_revision,
            fetched_at,
            document["source_sha256"],
            "chapter:core-docs",
            document["order_index"],
        ),
    )
    for section in document["sections"]:
        connection.execute(
            """
            INSERT OR REPLACE INTO sections(
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
                section["section_id"],
                snapshot_id,
                document["document_id"],
                section["chapter_id"],
                section["anchor"],
                section["heading"],
                section["order_index"],
                section["level"],
                section["text"],
                section["source_sha256"],
                fetched_at,
                source_revision,
            ),
        )
        chunk = section["chunk"]
        connection.execute(
            """
            INSERT OR REPLACE INTO chunks(
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
                chunk["chunk_uid"],
                section["section_id"],
                chunk["raw_text"],
                chunk["clean_text"],
                chunk["char_len"],
                chunk["token_len"],
                chunk["source_sha256"],
                fetched_at,
                source_revision,
                chunk["order_index"],
            ),
        )
        metadata = chunk["metadata"]
        connection.execute(
            """
            INSERT OR REPLACE INTO core_docs_chunk_metadata(
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
                chunk["chunk_uid"],
                metadata["item_path"],
                metadata["item_kind"],
                metadata["signature"],
                metadata["stability"],
                metadata["safety_notes"],
                metadata["panic_behavior"],
                metadata["example_snippets"],
                metadata["target_triple"],
                metadata["target_os"],
                metadata["target_arch"],
                metadata["target_env"],
                metadata["cfg_signature"],
                metadata["cfg_signature_sha256"],
            ),
        )
        span = chunk["span"]
        connection.execute(
            """
            INSERT OR REPLACE INTO chunk_spans(
                chunk_uid,
                source_anchor,
                start_offset,
                end_offset,
                span_order
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                chunk["chunk_uid"],
                span["source_anchor"],
                span["start_offset"],
                span["end_offset"],
                span["span_order"],
            ),
        )


def refresh_core_docs_fts(connection: sqlite3.Connection) -> None:
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
    enforce_chunk_fts_mapping(connection, context="core_docs incremental refresh")
