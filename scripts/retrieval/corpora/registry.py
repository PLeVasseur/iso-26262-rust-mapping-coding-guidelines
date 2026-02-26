from __future__ import annotations

from retrieval.corpora.base import CorpusAdapter
from retrieval.corpora.core_docs_adapter import CoreDocsAdapter
from retrieval.corpora.guidelines_repo_adapter import GuidelinesRepoAdapter
from retrieval.corpora.rust_reference_adapter import RustReferenceAdapter

_ADAPTERS: dict[str, CorpusAdapter] = {
    "rust_reference": RustReferenceAdapter(),
    "core_docs": CoreDocsAdapter(),
    "guidelines_repo": GuidelinesRepoAdapter(),
}


def list_supported_corpora() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTERS.keys()))


def get_corpus_adapter(corpus_name: str) -> CorpusAdapter:
    normalized = str(corpus_name).strip().lower()
    adapter = _ADAPTERS.get(normalized)
    if adapter is None:
        raise ValueError(
            f"Unsupported corpus '{corpus_name}'. Supported: {', '.join(list_supported_corpora())}"
        )
    return adapter
