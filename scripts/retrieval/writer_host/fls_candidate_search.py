from __future__ import annotations

from pathlib import Path
from typing import Any

from context.fls_lookup import search_fls_paragraphs
from retrieval.writer_host.fls_query_text import build_packet_query_text


def build_query_variants(packet: dict[str, Any]) -> list[dict[str, str]]:
    query = build_packet_query_text(packet)
    return [{"name": "packet_text", "query": query}] if query else []


def gather_candidates(
    *,
    packet: dict[str, Any],
    db_path: Path | None = None,
    limit_per_variant: int = 10,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    variants = build_query_variants(packet)
    rows: list[dict[str, Any]] = []
    for variant in variants:
        name = str(variant.get("name", "")).strip()
        query = str(variant.get("query", "")).strip()
        if not query:
            continue
        for row in search_fls_paragraphs(query, db_path=db_path, limit=limit_per_variant):
            rows.append(
                {
                    **row,
                    "variant_name": name,
                    "variant_query": query,
                }
            )
    return rows, variants
