from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _count_guidelines(export_root: Path) -> int:
    return sum(1 for path in export_root.rglob("gui_*.rst") if path.is_file())


def _count_duplicate_bib_entries(export_root: Path) -> int:
    count = 0
    for path in export_root.rglob("gui_*.rst"):
        lines = path.read_text(encoding="utf-8").splitlines()
        seen: set[str] = set()
        for line in lines:
            if ":bibentry:`" not in line:
                continue
            normalized = line.strip()
            if normalized in seen:
                count += 1
            else:
                seen.add(normalized)
    return count


def build_feedback_comparison(
    *,
    feedback_path: Path,
    export_root: Path,
    editorial_review_report: dict[str, Any],
    publishability_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    feedback_text = _read_text(feedback_path)
    category_counts = {
        code: len(re.findall(rf"`{re.escape(code)}`", feedback_text))
        for code in ("T", "N", "D", "M", "B", "V", "P", "E")
    }
    editorial_entries = list(editorial_review_report.get("entries") or [])
    title_flags = sum(
        1
        for entry in editorial_entries
        if any(
            str(issue).startswith("title_")
            for issue in list(entry.get("editorial_violations") or [])
        )
    )
    decomposition_flags = sum(
        1
        for entry in editorial_entries
        if "composite_rule_split_candidate" in list(entry.get("editorial_violations") or [])
    )
    evidence_blocks = sum(
        1
        for entry in editorial_entries
        if str(((entry.get("evidence_quality") or {}).get("status", ""))) == "fail"
    )
    chapter_flags = sum(
        1
        for entry in editorial_entries
        if any(
            str(issue).startswith("chapter_")
            for issue in list(entry.get("editorial_violations") or [])
        )
    )
    overlap_pairs = int(((editorial_review_report.get("overlap") or {}).get("pair_count", 0)) or 0)
    duplicate_bib_rows = _count_duplicate_bib_entries(export_root)
    publish_blocked = int(((publishability_audit or {}).get("blocked_count", 0)) or 0)
    return {
        "feedback_path": str(feedback_path),
        "export_root": str(export_root),
        "guideline_count": _count_guidelines(export_root),
        "feedback_category_mentions": category_counts,
        "current_signals": {
            "title_flags": title_flags,
            "composite_flags": decomposition_flags,
            "chapter_flags": chapter_flags,
            "evidence_blocks": evidence_blocks,
            "overlap_pairs": overlap_pairs,
            "duplicate_bibliography_rows": duplicate_bib_rows,
            "strict_publishability_blocked": publish_blocked,
        },
    }


def write_feedback_comparison(*, path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def render_feedback_comparison_summary(payload: dict[str, Any]) -> str:
    signals = dict(payload.get("current_signals") or {})
    lines = [
        "# Feedback Comparison Summary",
        "",
        f"- Guideline count: {int(payload.get('guideline_count', 0) or 0)}",
        f"- Title flags: {int(signals.get('title_flags', 0) or 0)}",
        f"- Composite-rule flags: {int(signals.get('composite_flags', 0) or 0)}",
        f"- Chapter flags: {int(signals.get('chapter_flags', 0) or 0)}",
        f"- Evidence-quality blocks: {int(signals.get('evidence_blocks', 0) or 0)}",
        f"- Overlap pairs: {int(signals.get('overlap_pairs', 0) or 0)}",
        f"- Duplicate bibliography rows: {int(signals.get('duplicate_bibliography_rows', 0) or 0)}",
        f"- Strict publishability blocked: {int(signals.get('strict_publishability_blocked', 0) or 0)}",
        "",
        "These signals are intended to compare the rerun batch against the categories raised in the reviewer feedback.",
    ]
    return "\n".join(lines) + "\n"
