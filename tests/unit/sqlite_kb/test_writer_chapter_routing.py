from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.chapter_routing import normalized_tags_for_domains, route_chapter  # noqa: E402


def test_route_chapter_places_atomic_rules_in_concurrency() -> None:
    routed = route_chapter(
        metadata={"tags": ["atomic-ordering", "concurrency"]},
        synth={"construct_scope": ["std::sync::atomic::Ordering", "std::sync::atomic::fence"]},
        title="Require release/acquire ordering for publication fences",
    )

    assert routed["chapter"] == "concurrency"


def test_normalized_tags_preserve_richer_domains() -> None:
    tags = normalized_tags_for_domains(
        metadata={"tags": ["must-use", "diagnostics"]},
        synth={"construct_scope": ["#[must_use]", "#[deny(unused_must_use)]"]},
        chapter="attributes",
    )

    assert "diagnostics" in tags
