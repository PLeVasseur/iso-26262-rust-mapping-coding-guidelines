from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from context.fls_lookup import resolve_fls_for_guideline, validate_fls_id

from retrieval.writer_host.chapter_routing import normalized_tags_for_domains, route_chapter
from retrieval.writer_host.fls_candidate_search import gather_candidates
from retrieval.writer_host.fls_resolution_packet import build_resolution_packet
from retrieval.writer_host.fls_resolution_report import write_resolution_report
from retrieval.writer_host.title_policy import derive_title


def _metadata_fls_candidate(metadata: dict[str, Any]) -> dict[str, Any]:
    raw = metadata.get("fls_candidate") if isinstance(metadata, dict) else {}
    return raw if isinstance(raw, dict) else {}


def _resolve_fls_id(
    *,
    packet: dict[str, Any],
    report_root: Path | None = None,
) -> tuple[str, dict[str, Any], str | None, dict[str, Any]]:
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
    valid = fls_id.startswith("fls_") and fls_id != "fls_UNRESOLVED" and validate_fls_id(fls_id)
    reason = str(paragraph.get("unresolved_reason", "")).strip() or str(
        decision.get("reason_code", "UNRESOLVED")
    )
    publishability = {
        "publishable": bool(valid),
        "reason_code": str(decision.get("reason_code", ""))
        or ("ACCEPTED" if valid else "UNRESOLVED"),
        "reason": "" if valid else reason,
        "resolved_paragraph_id": fls_id,
        "report_path": report_path or "",
        "decision": decision,
    }
    return (fls_id if valid else "fls_UNRESOLVED"), decision, report_path, publishability


def map_publish_record(
    row: dict[str, Any],
    *,
    resolution_report_root: Path | None = None,
    allow_unresolved: bool = False,
) -> dict[str, Any]:
    draft = row["draft"]
    metadata = row["metadata"]
    target_id = str(draft.get("target_id", "")).strip()
    if not target_id:
        raise RuntimeError("missing target_id for publish mapping")
    tags = list(metadata.get("tags") or [])
    editorial = metadata.get("editorial_metadata") if isinstance(metadata, dict) else {}
    pseudo_synth = {
        "construct_scope": list(draft.get("construct_terms") or []),
        "claim_to_evidence_map": list(draft.get("claim_to_evidence_map") or []),
    }
    title = (
        str(draft.get("title", "")).strip()
        or str((editorial or {}).get("proposed_title", "")).strip()
    )
    if not title:
        title = derive_title(
            target_id=target_id,
            synth=pseudo_synth,
            amplification=row.get("amplification")
            if isinstance(row.get("amplification"), dict)
            else {},
            metadata=metadata if isinstance(metadata, dict) else {},
        )
    routing = route_chapter(
        metadata=metadata if isinstance(metadata, dict) else {},
        synth=pseudo_synth,
        title=title,
        current_tags=tags,
    )
    chapter = str(draft.get("chapter", "")).strip() or str(routing.get("chapter", "expressions"))
    normalized_tags = normalized_tags_for_domains(
        metadata=metadata if isinstance(metadata, dict) else {},
        synth=pseudo_synth,
        chapter=chapter,
    )
    stable = hashlib.sha1(target_id.encode("utf-8")).hexdigest()[:12]
    guideline_id = f"gui_{stable}"
    filename = f"{guideline_id}.rst"
    fls_candidate = _metadata_fls_candidate(metadata)
    packet = build_resolution_packet(row)
    title = title or str(packet.get("title", "")).strip() or f"Guideline {target_id}"
    packet["title"] = title
    packet["expected_domains"] = normalized_tags
    fls_id, fls_resolution, fls_resolution_report, publishability = _resolve_fls_id(
        packet=packet,
        report_root=resolution_report_root,
    )
    if not bool(publishability.get("publishable", False)) and not allow_unresolved:
        raise RuntimeError(
            f"failed to resolve valid fls id for title='{title}': {publishability.get('reason', 'UNRESOLVED')}"
        )
    category_raw = str(fls_candidate.get("category", "")).strip().lower()
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
        "publishability": publishability,
        "decidability": "undecidable",
        "scope": "module",
        "tags": normalized_tags,
    }
