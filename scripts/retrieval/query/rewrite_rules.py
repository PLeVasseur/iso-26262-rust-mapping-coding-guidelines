from __future__ import annotations

from pathlib import Path
from typing import Any

from retrieval.core.rewrite import load_rewrite_rules, rewrite_query
from sqlite_query_guardrails import GuardrailError


def rewrite_query_text(
    *,
    query_text: str,
    row_marker: str,
    mode: str,
    rewrite_mode: str,
    rewrite_rules_path: Path,
) -> dict[str, Any]:
    try:
        return rewrite_query(
            query_text=query_text,
            row_marker=row_marker,
            mode=mode,
            rewrite_mode=rewrite_mode,
            rewrite_rules_path=rewrite_rules_path,
        )
    except ValueError as exc:
        raise GuardrailError(str(exc)) from exc
