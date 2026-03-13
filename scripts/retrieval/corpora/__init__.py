"""Corpus adapters for retrieval engine integration."""

from retrieval.corpora.registry import get_corpus_adapter, list_supported_corpora

__all__ = ["get_corpus_adapter", "list_supported_corpora"]
