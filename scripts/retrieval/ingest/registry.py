from __future__ import annotations

from retrieval.ingest.contracts import IngestStrategy
from retrieval.ingest.strategies.rust_md_v1 import RustMarkdownV1Strategy

_STRATEGIES: dict[str, IngestStrategy] = {
    "rust_md_v1": RustMarkdownV1Strategy(),
    "core_docs_pdf_v1": RustMarkdownV1Strategy(),
}


def list_ingest_strategies() -> tuple[str, ...]:
    return tuple(sorted(_STRATEGIES.keys()))


def resolve_ingest_strategy(strategy_id: str) -> IngestStrategy:
    normalized = str(strategy_id).strip().lower()
    strategy = _STRATEGIES.get(normalized)
    if strategy is None:
        raise RuntimeError(
            f"Unknown ingest strategy '{strategy_id}'. "
            f"Supported: {', '.join(list_ingest_strategies())}"
        )
    return strategy
