"""Ingest behavior strategies for corpus-specific cleaning/chunking."""

from retrieval.ingest.registry import list_ingest_strategies, resolve_ingest_strategy

__all__ = ["list_ingest_strategies", "resolve_ingest_strategy"]
