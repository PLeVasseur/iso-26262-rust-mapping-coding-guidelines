from __future__ import annotations

from retrieval.builders.base import BuilderRunner


def resolve_builder(corpus: str) -> BuilderRunner:
    normalized = str(corpus).strip().lower()
    if normalized == "rust_reference":
        from retrieval.operations import build as build_operation

        return build_operation.run_rust_reference_build
    if normalized == "core_docs":
        from retrieval.builders.core_docs_builder import run_core_docs_build

        return run_core_docs_build
    if normalized == "guidelines_repo":
        from retrieval.builders.guidelines_repo_builder import run_guidelines_repo_build

        return run_guidelines_repo_build
    raise RuntimeError(f"Unsupported build corpus: {corpus}")
