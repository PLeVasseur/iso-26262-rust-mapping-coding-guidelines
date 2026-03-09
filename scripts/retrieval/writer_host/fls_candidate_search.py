from __future__ import annotations

from pathlib import Path
from typing import Any

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
    raise RuntimeError(
        "WS7 ranking boundary requires direct execute_retrieval_query(...) rows; "
        "gather_candidates(...) legacy compatibility helper is retired"
    )
