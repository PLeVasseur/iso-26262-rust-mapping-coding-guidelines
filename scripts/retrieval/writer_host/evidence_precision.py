from __future__ import annotations

from typing import Any


def _clean(value: Any) -> str:
    return str(value or "").strip()


def evaluate_evidence_precision(bibliography_rows: list[dict[str, Any]]) -> dict[str, Any]:
    issues: list[str] = []
    warning_count = 0
    blocked = False
    urls = []
    for row in bibliography_rows:
        if not isinstance(row, dict):
            continue
        url = _clean(row.get("url"))
        if not url:
            continue
        urls.append(url)
        lowered = url.lower()
        if "search" in lowered and "doc.rust-lang.org" in lowered:
            issues.append("search_style_url")
            warning_count += 1
        if lowered.endswith("/core/error/") or lowered.endswith("/core/error/index.html"):
            issues.append("broad_module_url")
            warning_count += 1
        if lowered.endswith("/std/ptr/") or lowered.endswith("/std/ptr/index.html"):
            issues.append("broad_module_url")
            warning_count += 1
    if len(set(urls)) == 1 and len(urls) >= 3:
        issues.append("bibliography_padding_single_source")
        warning_count += 1
    unique_issues = sorted(dict.fromkeys(issues))
    status = "pass"
    if warning_count >= 3:
        status = "review"
    if "search_style_url" in unique_issues and warning_count >= 4:
        blocked = True
        status = "block"
    return {
        "status": status,
        "blocked": blocked,
        "warning_count": warning_count,
        "issues": unique_issues,
        "source_count": len(urls),
        "unique_source_count": len(set(urls)),
    }
