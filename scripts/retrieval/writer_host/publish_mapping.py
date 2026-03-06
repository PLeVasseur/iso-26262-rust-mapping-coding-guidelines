from __future__ import annotations

import hashlib
from typing import Any

from context.fls_lookup import resolve_fls_for_construct, validate_fls_id


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


def _resolve_fls_id(*, title: str, tags: list[str]) -> str:
    terms = [token for token in (title.split() + tags) if token]
    paragraph = resolve_fls_for_construct(terms)
    fls_id = str(paragraph.get("paragraph_id", "")).strip()
    if not fls_id.startswith("fls_") or fls_id == "fls_UNRESOLVED" or not validate_fls_id(fls_id):
        raise RuntimeError(f"failed to resolve valid fls id for title='{title}'")
    return fls_id


def map_publish_record(row: dict[str, Any]) -> dict[str, Any]:
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
    fls_id = _resolve_fls_id(title=title, tags=normalized_tags)
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
        "decidability": "undecidable",
        "scope": "module",
        "tags": normalized_tags,
    }
