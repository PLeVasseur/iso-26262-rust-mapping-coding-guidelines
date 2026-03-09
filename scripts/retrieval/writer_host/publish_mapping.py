from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from context.fls_lookup import resolve_fls_for_guideline, validate_fls_id

from retrieval.writer_host.chapter_routing import normalized_tags_for_domains, route_chapter
from retrieval.writer_host.fls_resolution_packet import build_resolution_packet
from retrieval.writer_host.fls_resolution_report import write_resolution_report
from retrieval.writer_host.reviewer_taxonomy import (
    classify_reviewer_family,
    normalize_reviewer_chapter,
)
from retrieval.writer_host.title_policy import derive_title


def _metadata_fls_candidate(metadata: dict[str, Any]) -> dict[str, Any]:
    raw = metadata.get("fls_candidate") if isinstance(metadata, dict) else {}
    return raw if isinstance(raw, dict) else {}


def _resolve_fls_id(
    *,
    packet: dict[str, Any],
    title: str,
    target_id: str,
    report_root: Path | None = None,
) -> tuple[str, dict[str, Any], str | None, dict[str, Any]]:
    paragraph = resolve_fls_for_guideline(packet)
    title = str(title).strip()
    target_id = str(target_id).strip() or "unknown"
    fls_id = str(paragraph.get("paragraph_id", "")).strip()
    decision = dict(paragraph.get("decision") or {})
    report_path: str | None = None
    if report_root is not None:
        report = {
            "runtime_mode": "grounding_only_ws6",
            "grounding_packet": packet,
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


def _infer_scope(*, family_key: str, title: str, tags: list[str]) -> str:
    text = " ".join([title.lower(), " ".join(value.lower() for value in tags)])
    if any(token in text for token in ("lint", "must_use", "forbid", "deny", "policy")):
        return "crate"
    if any(token in text for token in ("target_feature", "function", "unsafe fn", "unsafe trait")):
        return "function"
    if family_key in {"ownership_aliasing", "unsafety_boundary", "exceptions_errors"}:
        return "function"
    if family_key == "architecture_types":
        return "system"
    return "module"


def _infer_decidability(*, family_key: str, title: str, tags: list[str]) -> str:
    text = " ".join([title.lower(), " ".join(value.lower() for value in tags)])
    if any(
        token in text
        for token in ("must_use", "forbid", "deny", "target_feature", "expect", "raw pointer")
    ):
        return "decidable"
    if family_key in {"strict_provenance", "architecture_types"}:
        return "partially_decidable"
    if family_key in {"ownership_aliasing", "unsafety_boundary"}:
        return "partially_decidable"
    return "undecidable"


def _infer_category(*, category_raw: str, family_key: str, chapter: str) -> str:
    if "required" in category_raw or "safety" in category_raw:
        return "required"
    if family_key in {"unsafety_boundary", "ownership_aliasing"}:
        return "required"
    if chapter in {"concurrency", "unsafety"} and family_key != "strict_provenance":
        return "required"
    return "advisory"


def map_publish_record(
    row: dict[str, Any],
    *,
    resolution_report_root: Path | None = None,
    allow_unresolved: bool = False,
) -> dict[str, Any]:
    draft = row["draft"]
    metadata = row["metadata"]
    target_id = str(draft.get("target_id", "")).strip()
    atom_id = str(draft.get("atom_id", "")).strip()
    draft_id = str(draft.get("draft_id", "")).strip()
    if not target_id:
        raise RuntimeError("missing target_id for publish mapping")
    tags = list(metadata.get("tags") or [])
    editorial = metadata.get("editorial_metadata") if isinstance(metadata, dict) else {}
    pseudo_synth = {
        "construct_scope": list(draft.get("construct_terms") or []),
        "claim_to_evidence_map": list(draft.get("claim_to_evidence_map") or []),
    }
    amplification: dict[str, Any] = dict()
    amplification_raw = row.get("amplification")
    if isinstance(amplification_raw, dict):
        amplification = dict(amplification_raw)
    title = (
        str(draft.get("title", "")).strip()
        or str((editorial or {}).get("proposed_title", "")).strip()
    )
    if not title:
        title = derive_title(
            target_id=target_id,
            synth=pseudo_synth,
            amplification=amplification,
            metadata=metadata if isinstance(metadata, dict) else {},
        )
    routing = route_chapter(
        metadata=metadata if isinstance(metadata, dict) else {},
        synth=pseudo_synth,
        title=title,
        current_tags=tags,
    )
    chapter = normalize_reviewer_chapter(
        str(draft.get("chapter", "")).strip() or str(routing.get("chapter", "expressions"))
    )
    normalized_tags = normalized_tags_for_domains(
        metadata=metadata if isinstance(metadata, dict) else {},
        synth=pseudo_synth,
        chapter=chapter,
    )
    family_key = classify_reviewer_family(
        title=title,
        tags=[str(value) for value in tags],
        constructs=[str(value) for value in list(draft.get("construct_terms") or [])],
    )
    stable_seed = atom_id or draft_id or target_id
    stable = hashlib.sha1(stable_seed.encode("utf-8")).hexdigest()[:12]
    guideline_id = f"gui_{stable}"
    filename = f"{guideline_id}.rst"
    fls_candidate = _metadata_fls_candidate(metadata)
    packet = build_resolution_packet(row)
    title = title or f"Guideline {target_id}"
    fls_id, fls_resolution, fls_resolution_report, publishability = _resolve_fls_id(
        packet=packet,
        title=title,
        target_id=target_id,
        report_root=resolution_report_root,
    )
    if not bool(publishability.get("publishable", False)) and not allow_unresolved:
        raise RuntimeError(
            f"failed to resolve valid fls id for title='{title}': {publishability.get('reason', 'UNRESOLVED')}"
        )
    category_raw = str(fls_candidate.get("category", "")).strip().lower()
    category = _infer_category(category_raw=category_raw, family_key=family_key, chapter=chapter)
    return {
        "target_id": target_id,
        "atom_id": atom_id,
        "draft_id": draft_id,
        "guideline_id": guideline_id,
        "filename": filename,
        "chapter": chapter,
        "title": title,
        "category": category,
        "status": "draft",
        "release": str(metadata.get("release", "") or "1.85.1"),
        "fls_id": fls_id,
        "fls_resolution": fls_resolution,
        "fls_resolution_report": fls_resolution_report,
        "publishability": publishability,
        "decidability": _infer_decidability(
            family_key=family_key,
            title=title,
            tags=normalized_tags,
        ),
        "scope": _infer_scope(family_key=family_key, title=title, tags=normalized_tags),
        "tags": normalized_tags,
    }
