from __future__ import annotations

import sys
from pathlib import Path

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

    def fake_search(query: str, *, db_path=None, limit=5):
        return [
            {
                "paragraph_id": "fls_x",
                "text": f"hit for {query}",
                "chapter": "Unsafety",
                "section": "Raw Pointers",
                "paragraph_number": "19:2",
                "lexical_score": 0.77,
            }
        ]

    monkeypatch.setattr(fls_candidate_search, "search_fls_paragraphs", fake_search)
    rows, variants = fls_candidate_search.gather_candidates(packet=packet, limit_per_variant=2)

    assert variants
    assert rows
    assert variants == [{"name": "packet_text", "query": "Unsafe paths"}]
    assert all(str(row.get("variant_name", "")).strip() == "packet_text" for row in rows)
