from __future__ import annotations

from typing import Any


def _resolve_bibliography_urls(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for row in rows:
        locator = row.get("locator") if isinstance(row.get("locator"), dict) else {}
        url = str(row.get("url", "") or locator.get("url", "")).strip()
        out = dict(row)
        out["url"] = url if url else "URL_UNRESOLVED"
        resolved.append(out)
    return resolved


def _resolve_citations(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    resolved = _resolve_bibliography_urls(rows)
    keys: list[str] = []
    for row in resolved:
        key = str(row.get("citation_key", "")).strip()
        if key and key not in keys:
            keys.append(key)
    return resolved, keys


def _build_bibliography(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved_rows, citation_keys = _resolve_citations(rows)
    unresolved_count = sum(1 for row in resolved_rows if row.get("url") == "URL_UNRESOLVED")
    return {
        "bibliography_rows": resolved_rows,
        "citation_keys": citation_keys,
        "resolution_ok": bool(resolved_rows) and unresolved_count == 0,
        "unresolved_count": unresolved_count,
    }
