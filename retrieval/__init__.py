"""Compatibility package exposing scripts/retrieval as top-level retrieval."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
__path__ = [str(_ROOT / "scripts" / "retrieval")]
