from __future__ import annotations

from dataclasses import dataclass

from retrieval.ingest.contracts import ChunkInput, ChunkResult, CleanInput, CleanResult
from retrieval.ingest.strategies.rust_md_v1 import RustMarkdownV1Strategy


@dataclass(frozen=True)
class CoreDocsRustdocV1Strategy:
    strategy_id: str = "core_docs_rustdoc_v1"
    strategy_version: str = "1"

    def clean_text(self, clean_input: CleanInput) -> CleanResult:
        base = RustMarkdownV1Strategy()
        result = base.clean_text(clean_input)
        return CleanResult(
            cleaned_text=result.cleaned_text,
            normalizer_version="core-docs-rustdoc-clean-v1",
        )

    def build_chunks(self, chunk_input: ChunkInput) -> ChunkResult:
        base = RustMarkdownV1Strategy()
        result = base.build_chunks(chunk_input)
        return ChunkResult(
            chunks=result.chunks,
            spans=result.spans,
            strategy_version=self.strategy_version,
        )
