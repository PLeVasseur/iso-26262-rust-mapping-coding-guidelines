from __future__ import annotations

from typing import Any

from retrieval.writer_host.editorial_overlap import analyze_overlap


def build_editorial_review_report(entries: list[dict[str, Any]]) -> dict[str, Any]:
    overlap = analyze_overlap(entries)
    per_target = []
    blocked = 0
    review = 0
    for entry in entries:
        evidence = dict(entry.get("evidence_quality") or {})
        decomposition = dict(entry.get("decomposition") or {})
        violations = list(entry.get("editorial_violations") or [])
        status = "pass"
        if evidence.get("blocked") or "evidence_quality_blocked" in violations:
            status = "fail"
            blocked += 1
        elif violations or str(decomposition.get("status", "")) == "review":
            status = "review"
            review += 1
        per_target.append(
            {
                "target_id": str(entry.get("target_id", "")),
                "title": str(entry.get("title", "")),
                "chapter": str(entry.get("chapter", "")),
                "status": status,
                "editorial_violations": violations,
                "evidence_quality": evidence,
                "decomposition": decomposition,
            }
        )
    status = "pass" if blocked == 0 and not overlap.get("pairs") else "review"
    return {
        "status": status,
        "blocked_count": blocked,
        "review_count": review,
        "target_count": len(entries),
        "overlap": overlap,
        "entries": per_target,
    }
