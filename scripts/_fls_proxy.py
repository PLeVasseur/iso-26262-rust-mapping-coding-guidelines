from __future__ import annotations

import hashlib
import re
from typing import Any


def classify_target_class(target_id: str) -> str:
    lowered = target_id.lower()
    if ":table_" in lowered:
        return "table"
    if ":annex_" in lowered or ":annex:" in lowered:
        return "annex"
    return "clause"


def normalize_obligation_unit(seed: dict[str, Any]) -> str:
    row_key = str(seed.get("row_key") or "").strip()
    if row_key:
        return row_key

    anchor = str(seed.get("citation_anchor_id") or "").strip()
    if anchor:
        return anchor

    chunk_id = str(seed.get("chunk_id") or "").strip()
    if chunk_id:
        return chunk_id

    material = "|".join(
        [
            str(seed.get("seed_id") or ""),
            str(seed.get("iso_ref") or ""),
            str(seed.get("reference") or ""),
        ]
    )
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]
    return f"obl_{digest}"


def rule_family_id_for_target(target_id: str) -> str:
    digest = hashlib.sha1(target_id.encode("utf-8")).hexdigest()[:12].upper()
    return f"FAM-{digest}"


def guideline_id_for_scaffold(seed_id: str, target_id: str, ordinal: int) -> str:
    material = f"{seed_id}|{target_id}|{ordinal}"
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:12].upper()
    return f"RG-{digest}"


def slug_ascii(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized or "misc"
