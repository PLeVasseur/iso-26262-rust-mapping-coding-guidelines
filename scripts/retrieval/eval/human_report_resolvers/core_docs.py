from __future__ import annotations

import sqlite3

from retrieval.eval.human_report_resolvers.base import ChunkColumn, ChunkRecord


class CoreDocsHumanReportResolver:
    corpus = "core_docs"
    extra_columns = (
        ChunkColumn("item_path", "item_path"),
        ChunkColumn("item_kind", "item_kind"),
        ChunkColumn("target_triple", "target_triple"),
    )

    def fetch_chunk_records(
        self,
        *,
        conn: sqlite3.Connection,
        chunk_ids: list[str],
        snippet_chars: int,
    ) -> dict[str, ChunkRecord]:
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" for _ in chunk_ids)
        query = f"""
            SELECT
                c.chunk_uid,
                COALESCE(sec.heading, '') AS section_heading,
                COALESCE(sp.source_anchor, sd.rel_path || '#' || sec.anchor) AS source_anchor,
                COALESCE(m.item_path, '') AS item_path,
                COALESCE(m.item_kind, '') AS item_kind,
                COALESCE(m.target_triple, '') AS target_triple,
                COALESCE(c.clean_text, '') AS clean_text
            FROM chunks AS c
            JOIN sections AS sec ON sec.section_id = c.section_id
            JOIN source_documents AS sd ON sd.document_id = sec.document_id
            LEFT JOIN chunk_spans AS sp
              ON sp.chunk_uid = c.chunk_uid
             AND sp.span_order = 1
            LEFT JOIN core_docs_chunk_metadata AS m
              ON m.chunk_uid = c.chunk_uid
            WHERE c.chunk_uid IN ({placeholders})
        """
        rows = conn.execute(query, chunk_ids).fetchall()

        records: dict[str, ChunkRecord] = {}
        for row in rows:
            chunk_uid = str(row[0])
            records[chunk_uid] = ChunkRecord(
                chunk_uid=chunk_uid,
                section_heading=str(row[1]),
                source_anchor=str(row[2]),
                snippet=str(row[6])[:snippet_chars],
                extras={
                    "item_path": str(row[3]),
                    "item_kind": str(row[4]),
                    "target_triple": str(row[5]),
                },
            )
        return records
