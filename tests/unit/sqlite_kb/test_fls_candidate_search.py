from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host import fls_candidate_search  # noqa: E402


def test_build_query_variants_includes_multifield_channels() -> None:
    packet = {
        "title": "Unsafe error recovery can violate invariants",
        "amplification_text": "Safety invariants must hold on fault paths",
        "rationale_text": "Unchecked pointer use can trigger UB",
        "non_compliant_narrative": "logs and continues",
        "non_compliant_code": "unsafe { *ptr }",
        "compliant_narrative": "returns Result on invalid state",
        "compliant_code": "slice.get(idx)",
        "claim_phrases": ["safe callers must not trigger UB"],
    }

    variants = fls_candidate_search.build_query_variants(packet)
    names = {row["name"] for row in variants}
    assert "title_focus" in names
    assert "rationale_focus" in names
    assert "unsafe_code_focus" in names
    assert "claim_focus" in names
    assert "hybrid_focus" in names


def test_gather_candidates_tags_variant_name(monkeypatch) -> None:
    packet = {
        "title": "Unsafe paths",
        "amplification_text": "",
        "rationale_text": "",
        "non_compliant_narrative": "",
        "non_compliant_code": "",
        "compliant_narrative": "",
        "compliant_code": "",
        "claim_phrases": [],
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
    assert all(str(row.get("variant_name", "")).strip() for row in rows)
