from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.fls_resolution_packet import build_resolution_packet  # noqa: E402


def test_build_resolution_packet_uses_all_writer_fields() -> None:
    row = {
        "draft": {
            "target_id": "RET-ISSUE-001",
            "title": "Encode error-path invariants in checked APIs",
            "claim_to_evidence_map": [
                {"claim_text": "Unsafe fallback may violate pointer invariants."}
            ],
        },
        "amplification": {
            "guideline_amplification_text": "Recovery paths must preserve invariants."
        },
        "rationale": {"rationale_text": "Weak checks can expose UB in safe-callable paths."},
        "examples": {
            "non_compliant_narrative": "Continues after parse error and uses unchecked indexing.",
            "non_compliant_code": "unsafe { *values.get_unchecked(idx) }",
            "compliant_narrative": "Returns explicit errors before unsafe operations.",
            "compliant_code": 'values.get(idx).copied().ok_or("index")',
        },
        "metadata": {
            "tags": ["unsafe", "error-handling"],
            "fls_candidate": {"statement": "Weak defensive handling can lead to unsafe UB paths"},
        },
    }

    packet = build_resolution_packet(row)

    assert packet["target_id"] == "RET-ISSUE-001"
    assert packet["title"] == "Encode error-path invariants in checked APIs"
    assert packet["amplification_text"]
    assert packet["rationale_text"]
    assert "get_unchecked" in packet["non_compliant_code"]
    assert packet["claim_phrases"]
    assert "unsafe" in packet["expected_domains"]
    assert "title" in packet["field_terms"]
    assert "claim" in packet["field_terms"]
    assert packet["code_symbols"]
