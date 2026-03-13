from __future__ import annotations

import re
from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list_text(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        text = _text(value)
        if text:
            out.append(text)
    return out


def build_packet_query_text(packet: dict[str, Any]) -> str:
    fields = [
        _text(packet.get("governing_obligation")),
        " ".join(_list_text(packet.get("supporting_phrases"))),
        " ".join(_list_text(packet.get("construct_terms"))),
        " ".join(_list_text(packet.get("code_tokens"))),
    ]
    merged = " ".join(value for value in fields if value)
    return " ".join(re.findall(r"[A-Za-z0-9_]+", merged)).strip()
