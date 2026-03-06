from __future__ import annotations

import hashlib
from typing import Any


def _chapter_from_tags(tags: list[str]) -> str:
    lowered = [str(tag).strip().lower() for tag in tags if str(tag).strip()]
    if any("unsafe" in tag for tag in lowered):
        return "unsafety"
    if any("error" in tag for tag in lowered):
        return "exceptions-and-errors"
    if any("macro" in tag for tag in lowered):
        return "macros"
    return "expressions"


def map_publish_record(row: dict[str, Any]) -> dict[str, Any]:
    draft = row["draft"]
    metadata = row["metadata"]
    target_id = str(draft.get("target_id", "")).strip()
    if not target_id:
        raise RuntimeError("missing target_id for publish mapping")
    tags = list(metadata.get("tags") or [])
    chapter = _chapter_from_tags(tags)
    stable = hashlib.sha1(target_id.encode("utf-8")).hexdigest()[:12]
    guideline_id = f"gui_{stable}"
    filename = f"{guideline_id}.rst"
    fls_candidate = metadata.get("fls_candidate") if isinstance(metadata, dict) else {}
    title = str((fls_candidate or {}).get("statement", "")).strip() or f"Guideline {target_id}"
    return {
        "target_id": target_id,
        "guideline_id": guideline_id,
        "filename": filename,
        "chapter": chapter,
        "title": title,
    }
