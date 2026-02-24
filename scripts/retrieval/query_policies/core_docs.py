from __future__ import annotations

from typing import Any

TARGET_HINT_ALIASES: dict[str, tuple[str, ...]] = {
    "qnx": ("qnx", "nto", "nto71", "nto80", "qnx710", "qnx800"),
    "vxworks": ("vxworks", "wrs-vxworks"),
    "embedded": ("embedded", "no_std", "thumbv7em"),
}

TARGET_HINT_MATCHERS: dict[str, tuple[str, ...]] = {
    "qnx": ("nto-qnx", "qnx"),
    "vxworks": ("wrs-vxworks", "vxworks"),
    "embedded": ("thumbv7em", "none-eabi", "no_std"),
}


def _detect_target_hint_family(query_text: str) -> str:
    lowered = str(query_text).strip().lower()
    for family, aliases in TARGET_HINT_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            return family
    return ""


def apply_target_hint_preference(
    rows: list[dict[str, Any]], *, query_text: str
) -> list[dict[str, Any]]:
    family = _detect_target_hint_family(query_text)
    if not family:
        return rows
    matchers = TARGET_HINT_MATCHERS.get(family, ())

    def _matches(row: dict[str, Any]) -> bool:
        blob = " ".join(
            (
                str(row.get("source_anchor", "")),
                str(row.get("chunk_text", "")),
                str(row.get("target_triple", "")),
                str(row.get("target_env", "")),
            )
        ).lower()
        return any(token in blob for token in matchers)

    decorated = [(1 if _matches(row) else 0, idx, row) for idx, row in enumerate(rows)]
    decorated.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in decorated]
