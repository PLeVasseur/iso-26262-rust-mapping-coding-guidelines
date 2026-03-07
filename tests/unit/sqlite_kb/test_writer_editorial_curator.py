from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.editorial_curator import (  # noqa: E402
    normalize_editorial_curation,
    validate_editorial_curation,
)


def test_validate_editorial_curation_accepts_keep_decision() -> None:
    output = {
        "target_id": "RET-ISSUE-006",
        "family_id": "RET-ISSUE-006",
        "decision_summary": "Keep atom.",
        "decision_confidence": "high",
        "atom_decisions": [
            {
                "atom_id": "RET-ISSUE-006::atom::lint-enforcement",
                "draft_id": "draft::RET-ISSUE-006::atom::lint-enforcement",
                "disposition": "keep",
                "decision_confidence": "high",
                "batch_overlap_decision": {"status": "clear", "compared_atoms": []},
                "baseline_overlap_decision": {"status": "clear", "overlapping_guidelines": []},
                "final_why": "distinct",
                "export_recommendation": "export",
            }
        ],
    }

    assert (
        validate_editorial_curation(
            output=output,
            known_draft_ids={"draft::RET-ISSUE-006::atom::lint-enforcement"},
            known_atom_ids={"RET-ISSUE-006::atom::lint-enforcement"},
        )
        == []
    )


def test_normalize_editorial_curation_falls_back_from_atom_packages() -> None:
    normalized = normalize_editorial_curation(
        target_id="RET-NEG-001",
        output={
            "target_id": "RET-NEG-001",
            "family_id": "RET-NEG-001",
            "decision_summary": "fallback",
            "decision_confidence": "low",
            "atom_decisions": [],
        },
        atom_packages=[
            {
                "atom_id": "RET-NEG-001::atom::candidate",
                "draft_id": "draft::RET-NEG-001::atom::candidate",
                "evidence_quality": {"blocked": True},
            }
        ],
    )

    assert normalized["atom_decisions"][0]["disposition"] == "abstain"
    assert normalized["atom_decisions"][0]["export_recommendation"] == "do_not_export"


def test_normalize_editorial_curation_maps_drop_and_overlap_synonyms() -> None:
    normalized = normalize_editorial_curation(
        target_id="RET-NEG-001",
        output={
            "target_id": "RET-NEG-001",
            "family_id": "RET-NEG",
            "decision_summary": "drop unsupported atom",
            "decision_confidence": "medium",
            "atom_decisions": [
                {
                    "atom_id": "RET-NEG-001::atom::candidate",
                    "draft_id": "draft::RET-NEG-001::atom::candidate",
                    "disposition": "drop",
                    "batch_overlap_decision": {"status": "no_overlap"},
                    "baseline_overlap_decision": {"status": "no_material_overlap"},
                }
            ],
        },
        atom_packages=[
            {
                "atom_id": "RET-NEG-001::atom::candidate",
                "draft_id": "draft::RET-NEG-001::atom::candidate",
                "evidence_quality": {"blocked": True},
            }
        ],
    )

    decision = normalized["atom_decisions"][0]
    assert decision["disposition"] == "drop_low_evidence_support"
    assert decision["batch_overlap_decision"]["status"] == "clear"
    assert decision["baseline_overlap_decision"]["status"] == "clear"
    assert (
        validate_editorial_curation(
            output=normalized,
            known_draft_ids={"draft::RET-NEG-001::atom::candidate"},
            known_atom_ids={"RET-NEG-001::atom::candidate"},
        )
        == []
    )


def test_normalize_editorial_curation_uses_decision_field_and_baseline_residue_status() -> None:
    normalized = normalize_editorial_curation(
        target_id="RET-RESOLVE-006",
        output={
            "target_id": "RET-RESOLVE-006",
            "family_id": "attributes",
            "decision_summary": "keep both",
            "decision_confidence": "high",
            "atom_decisions": [
                {
                    "atom_id": "RET-RESOLVE-006::atom::lint-level-subset",
                    "draft_id": "draft::RET-RESOLVE-006::atom::lint-level-subset",
                    "decision": "keep",
                    "decision_reason": "Distinct lint subset atom.",
                    "baseline_overlap_decision": {"status": "meaningful_residue"},
                }
            ],
        },
        atom_packages=[
            {
                "atom_id": "RET-RESOLVE-006::atom::lint-level-subset",
                "draft_id": "draft::RET-RESOLVE-006::atom::lint-level-subset",
                "evidence_quality": {"blocked": False},
            }
        ],
    )

    decision = normalized["atom_decisions"][0]
    assert decision["disposition"] == "keep"
    assert decision["baseline_overlap_decision"]["status"] == "partial_but_keep"
    assert decision["final_why"] == "Distinct lint subset atom."
    assert decision["export_recommendation"] == "export"


def test_normalize_editorial_curation_maps_human_review_and_distinct_status() -> None:
    normalized = normalize_editorial_curation(
        target_id="RET-ISSUE-006",
        output={
            "target_id": "RET-ISSUE-006",
            "family_id": "attributes",
            "decision_summary": "hold for review",
            "decision_confidence": "medium",
            "atom_decisions": [
                {
                    "atom_id": "RET-ISSUE-006::atom::1",
                    "draft_id": "draft::RET-ISSUE-006::atom::1",
                    "decision": "human_review",
                    "disposition": "human_review",
                    "baseline_overlap_decision": {"status": "distinct"},
                }
            ],
        },
        atom_packages=[
            {
                "atom_id": "RET-ISSUE-006::atom::1",
                "draft_id": "draft::RET-ISSUE-006::atom::1",
                "evidence_quality": {"blocked": False},
            }
        ],
    )

    decision = normalized["atom_decisions"][0]
    assert decision["disposition"] == "needs_human_review"
    assert decision["baseline_overlap_decision"]["status"] == "clear"
