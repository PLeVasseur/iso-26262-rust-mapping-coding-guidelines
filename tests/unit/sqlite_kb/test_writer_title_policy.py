from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.title_policy import derive_title, title_leakage_codes  # noqa: E402


def test_title_leakage_codes_flags_process_and_source_sentence_titles() -> None:
    assert "title_process_note" in title_leakage_codes(
        "The cited evidence for this run is off-target: it describes the unary ! operation"
    )
    assert "title_source_sentence_shape" in title_leakage_codes(
        "Every variable, item, and value in a Rust program has a type, and the type defines the interpretation of the memory holding it"
    )


def test_derive_title_prefers_mitigation_style_rule_title() -> None:
    title = derive_title(
        target_id="RET-ISSUE-006",
        synth={
            "construct_scope": ["#[must_use]"],
            "mitigation": "Require lint settings that make ignored must-use results fail verification reviews.",
        },
        amplification={},
        metadata={},
    )

    assert (
        title
        == "Require lint settings that make ignored must-use results fail verification reviews"
    )
