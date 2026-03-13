from __future__ import annotations

from retrieval.eval.human_report_resolvers.base import HumanReportResolver
from retrieval.eval.human_report_resolvers.core_docs import CoreDocsHumanReportResolver
from retrieval.eval.human_report_resolvers.rust_reference import RustReferenceHumanReportResolver

_RESOLVERS: dict[str, HumanReportResolver] = {
    "core_docs": CoreDocsHumanReportResolver(),
    "rust_reference": RustReferenceHumanReportResolver(),
}


def get_human_report_resolver(corpus: str) -> HumanReportResolver:
    normalized = str(corpus).strip().lower()
    resolver = _RESOLVERS.get(normalized)
    if resolver is None:
        supported = ", ".join(sorted(_RESOLVERS.keys()))
        raise RuntimeError(f"No eval-report resolver for corpus '{corpus}'. Supported: {supported}")
    return resolver
