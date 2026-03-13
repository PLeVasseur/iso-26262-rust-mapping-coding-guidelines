from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.editorial_decomposition import assess_decomposition  # noqa: E402
from retrieval.writer_host.editorial_overlap import analyze_overlap  # noqa: E402
from retrieval.writer_host.evidence_quality_gate import evaluate_evidence_quality  # noqa: E402
from retrieval.writer_host.editorial_validation import validate_editorial_bundle  # noqa: E402


def test_evidence_quality_gate_blocks_off_target_negative_controls() -> None:
    report = evaluate_evidence_quality(
        target_id="RET-NEG-001",
        query_text="zzzxxyyqqq qqqzzz nohits",
        synth={"construct_scope": ["std::sync::atomic::Ordering"]},
        metadata={"metadata_validation_notes": ["The evidence is off-target and mismatched."]},
        evidence_rows=[{"statement_text": "The not operation performs unary !."}],
    )

    assert report["blocked"] is True
    assert "off_target_evidence" in report["issues"]


def test_assess_decomposition_flags_composite_rule_candidates() -> None:
    report = assess_decomposition(
        target_id="RET-ISSUE-010",
        synth={"construct_scope": ["unsafe fn", "transmute", "lifetime elision"]},
        amplification={
            "guideline_amplification_text": "Unsafe code shall document preconditions and shall avoid lifetime extension through transmute rather than broad caller convention."
        },
        metadata={"tags": ["unsafe", "lifetime", "ownership", "aliasing", "transmute"]},
    )

    assert report["status"] == "split_candidate"


def test_analyze_overlap_detects_near_duplicates() -> None:
    report = analyze_overlap(
        [
            {
                "target_id": "A",
                "title": "Require lint settings that fail ignored must-use values",
                "chapter": "attributes",
                "construct_terms": ["#[must_use]", "lint"],
                "claim_text_blob": "ignored must_use values should fail review",
            },
            {
                "target_id": "B",
                "title": "Require lint policy that rejects ignored must-use results",
                "chapter": "attributes",
                "construct_terms": ["#[must_use]", "lint"],
                "claim_text_blob": "ignored must_use results should fail review",
            },
        ]
    )

    assert report["pair_count"] == 1
    assert report["pairs"][0]["kind"] == "near_duplicate"


def test_validate_editorial_bundle_reports_title_and_evidence_blocks() -> None:
    violations = validate_editorial_bundle(
        target_id="RET-NEG-001",
        draft={
            "title": "The cited evidence for this run is off-target: it describes the unary ! operation.",
            "chapter": "expressions",
            "review_question": "",
        },
        metadata={"tags": ["concurrency"]},
        synth={"construct_scope": ["std::sync::atomic::Ordering"]},
        evidence_quality={"blocked": True},
        decomposition={"status": "split_candidate"},
    )

    assert "title_process_note" in violations
    assert "review_question_missing" in violations
    assert "evidence_quality_blocked" in violations
    assert "composite_rule_split_candidate" in violations
