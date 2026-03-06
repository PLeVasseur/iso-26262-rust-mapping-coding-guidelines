from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from context.fls_lookup import resolve_fls_for_guideline, validate_fls_id

from retrieval.writer_host.fls_candidate_search import gather_candidates
from retrieval.writer_host.fls_resolution_packet import build_resolution_packet
from retrieval.writer_host.fls_resolution_report import write_resolution_report


def _chapter_from_tags(tags: list[str]) -> str:
    lowered = [str(tag).strip().lower() for tag in tags if str(tag).strip()]
    if any("unsafe" in tag for tag in lowered):
        return "unsafety"
    if any("error" in tag for tag in lowered):
        return "exceptions-and-errors"
    if any("macro" in tag for tag in lowered):
        return "macros"
    return "expressions"


def _normalized_tags(tags: list[str]) -> list[str]:
    lowered = [str(tag).strip().lower() for tag in tags if str(tag).strip()]
    out: list[str] = []
    if any("unsafe" in tag for tag in lowered):
        out.append("unsafe")
    if any("error" in tag for tag in lowered):
        out.append("defect")
    if not out:
        out.append("subset")
    return out


def _resolve_fls_id(
    *,
    packet: dict[str, Any],
    report_root: Path | None = None,
) -> tuple[str, dict[str, Any], str | None]:
    candidates, variants = gather_candidates(packet=packet)
    paragraph = resolve_fls_for_guideline(
        packet,
        precomputed_candidates=candidates,
        precomputed_variants=variants,
    )
    title = str(packet.get("title", "")).strip()
    target_id = str(packet.get("target_id", "")).strip() or "unknown"
    fls_id = str(paragraph.get("paragraph_id", "")).strip()
    decision = dict(paragraph.get("decision") or {})
    report_path: str | None = None
    if report_root is not None:
        report = {
            "variants": list(variants),
            "candidate_count": len(candidates),
            "candidate_preview": [
                {
                    "paragraph_id": str(row.get("paragraph_id", "")),
                    "chapter": str(row.get("chapter", "")),
                    "lexical_score": float(row.get("lexical_score", 0.0) or 0.0),
                    "variant_name": str(row.get("variant_name", "")),
                }
                for row in candidates[:15]
            ],
            "decision": decision,
            "resolved_paragraph_id": fls_id,
            "unresolved_reason": str(paragraph.get("unresolved_reason", "")),
        }
        report_path = str(
            write_resolution_report(
                report_root=report_root,
                target_id=target_id,
                title=title,
                payload=report,
            )
        )
    if not fls_id.startswith("fls_") or fls_id == "fls_UNRESOLVED" or not validate_fls_id(fls_id):
        reason = str(paragraph.get("unresolved_reason", "")).strip() or str(
            decision.get("reason_code", "UNRESOLVED")
        )
        raise RuntimeError(f"failed to resolve valid fls id for title='{title}': {reason}")
    return fls_id, decision, report_path


def map_publish_record(
    row: dict[str, Any],
    *,
    resolution_report_root: Path | None = None,
) -> dict[str, Any]:
    draft = row["draft"]
    metadata = row["metadata"]
    target_id = str(draft.get("target_id", "")).strip()
    if not target_id:
        raise RuntimeError("missing target_id for publish mapping")
    tags = list(metadata.get("tags") or [])
    chapter = _chapter_from_tags(tags)
    normalized_tags = _normalized_tags(tags)
    stable = hashlib.sha1(target_id.encode("utf-8")).hexdigest()[:12]
    guideline_id = f"gui_{stable}"
    filename = f"{guideline_id}.rst"
    fls_candidate = metadata.get("fls_candidate") if isinstance(metadata, dict) else {}
    title = str((fls_candidate or {}).get("statement", "")).strip() or f"Guideline {target_id}"
    packet = build_resolution_packet(row)
    packet["title"] = title
    packet["expected_domains"] = normalized_tags
    fls_id, fls_resolution, fls_resolution_report = _resolve_fls_id(
        packet=packet,
        report_root=resolution_report_root,
    )
    category_raw = str((fls_candidate or {}).get("category", "")).strip().lower()
    category = "required" if "required" in category_raw or "safety" in category_raw else "advisory"
    return {
        "target_id": target_id,
        "guideline_id": guideline_id,
        "filename": filename,
        "chapter": chapter,
        "title": title,
        "category": category,
        "status": "draft",
        "release": "1.85.1",
        "fls_id": fls_id,
        "fls_resolution": fls_resolution,
        "fls_resolution_report": fls_resolution_report,
        "decidability": "undecidable",
        "scope": "module",
        "tags": normalized_tags,
    }
