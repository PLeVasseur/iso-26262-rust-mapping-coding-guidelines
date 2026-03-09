from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host import fls_candidate_search  # noqa: E402


def test_build_query_variants_uses_single_packet_text_query() -> None:
    packet = {
        "governing_obligation": "Unsafe error recovery can violate invariants",
        "supporting_phrases": [
            "safe callers must not trigger UB",
            "Safety invariants must hold on fault paths",
        ],
        "construct_terms": ["unsafe", "invariants"],
        "code_tokens": ["ptr", "slice", "idx"],
    }

    variants = fls_candidate_search.build_query_variants(packet)
    assert variants == [
        {
            "name": "packet_text",
            "query": (
                "Unsafe error recovery can violate invariants "
                "safe callers must not trigger UB "
                "Safety invariants must hold on fault paths unsafe invariants ptr slice idx"
            ),
        }
    ]


def test_gather_candidates_tags_variant_name(monkeypatch) -> None:
    packet = {
        "governing_obligation": "Unsafe paths",
        "supporting_phrases": [],
        "construct_terms": [],
        "code_tokens": [],
    }

    with pytest.raises(RuntimeError, match="legacy compatibility helper is retired"):
        fls_candidate_search.gather_candidates(packet=packet, limit_per_variant=2)
