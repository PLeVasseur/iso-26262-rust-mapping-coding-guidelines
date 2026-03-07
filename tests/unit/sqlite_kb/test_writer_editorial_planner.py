from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.editorial_planner import (  # noqa: E402
    flatten_planned_atoms,
    normalize_editorial_plan,
    validate_editorial_plan,
)


def test_validate_editorial_plan_accepts_writeable_atoms() -> None:
    output = {
        "target_id": "RET-ISSUE-006",
        "decision": "emit_one",
        "decision_confidence": "high",
        "decision_rationale": "One rule is enough.",
        "rule_atoms": [
            {
                "atom_id": "RET-ISSUE-006::atom::lint-enforcement",
                "disposition": "write",
                "title": "Require verification-relevant lints to fail review or CI",
                "review_question": "Do verification-relevant lints fail review or CI?",
                "chapter": "attributes",
                "primary_construct_family": "diagnostics",
                "hazard_focus": "hazard",
                "mechanism_focus": "mechanism",
                "mitigation_focus": "mitigation",
                "why_distinct": "distinct rule",
                "evidence_ids": ["rust_reference::stmt-1"],
                "claim_ids": ["RET-ISSUE-006::claim::1"],
                "writer_brief": "Keep narrow.",
                "batch_overlap": {"status": "none", "candidates": []},
                "baseline_overlap": {"status": "low", "candidates": []},
                "write_recommendation": "write",
            }
        ],
    }

    assert validate_editorial_plan(output=output, evidence_ids={"rust_reference::stmt-1"}) == []


def test_normalize_editorial_plan_falls_back_to_single_atom() -> None:
    normalized = normalize_editorial_plan(
        target_id="RET-ISSUE-006",
        query_text="lint enforcement",
        output={
            "target_id": "RET-ISSUE-006",
            "decision": "emit_one",
            "decision_confidence": "low",
            "decision_rationale": "fallback",
            "rule_atoms": [],
        },
        synth={
            "hazard": "hazard",
            "mechanism": "mechanism",
            "mitigation": "Require lint escalation for verification findings.",
            "evidence_ids": ["rust_reference::stmt-1"],
            "claim_to_evidence_map": [
                {"claim_id": "RET-ISSUE-006::claim::1", "claim_text": "claim"}
            ],
        },
        baseline_candidates=[],
    )

    assert normalized["rule_atoms"]
    flattened = flatten_planned_atoms(normalized)
    assert flattened[0]["draft_id"].startswith("draft::RET-ISSUE-006::atom::")


def test_normalize_editorial_plan_maps_export_disposition_to_write() -> None:
    normalized = normalize_editorial_plan(
        target_id="RET-ISSUE-006",
        query_text="lint enforcement",
        output={
            "target_id": "RET-ISSUE-006",
            "decision": "emit_one",
            "decision_confidence": "high",
            "decision_rationale": "one rule",
            "rule_atoms": [
                {
                    "atom_id": "RET-ISSUE-006::atom::lint-enforcement",
                    "disposition": "export",
                    "title": "Require diagnostics enforcement",
                    "review_question": "Are diagnostics enforced?",
                    "chapter": "attributes",
                    "primary_construct_family": "diagnostics",
                    "evidence_ids": ["rust_reference::stmt-1"],
                    "claim_ids": ["RET-ISSUE-006::claim::1"],
                }
            ],
        },
        synth={
            "hazard": "hazard",
            "mechanism": "mechanism",
            "mitigation": "mitigation",
            "evidence_ids": ["rust_reference::stmt-1"],
            "claim_to_evidence_map": [
                {"claim_id": "RET-ISSUE-006::claim::1", "claim_text": "claim"}
            ],
        },
        baseline_candidates=[],
    )

    assert normalized["rule_atoms"][0]["disposition"] == "write"
    assert (
        validate_editorial_plan(
            output=normalized,
            evidence_ids={"rust_reference::stmt-1"},
        )
        == []
    )


def test_normalize_editorial_plan_maps_diagnostics_chapter_alias() -> None:
    normalized = normalize_editorial_plan(
        target_id="RET-RESOLVE-006",
        query_text="diagnostics enforcement",
        output={
            "target_id": "RET-RESOLVE-006",
            "decision": "emit_one",
            "decision_confidence": "high",
            "decision_rationale": "one rule",
            "rule_atoms": [
                {
                    "atom_id": "RET-RESOLVE-006::atom::lint-levels",
                    "disposition": "write",
                    "title": "Enforce subset lints with deny or forbid",
                    "review_question": "Are subset lints enforced?",
                    "chapter": "attributes-and-diagnostics",
                    "primary_construct_family": "diagnostics",
                    "evidence_ids": ["rust_reference::stmt-1"],
                    "claim_ids": ["RET-RESOLVE-006::claim::1"],
                }
            ],
        },
        synth={
            "hazard": "hazard",
            "mechanism": "mechanism",
            "mitigation": "mitigation",
            "evidence_ids": ["rust_reference::stmt-1"],
            "claim_to_evidence_map": [
                {"claim_id": "RET-RESOLVE-006::claim::1", "claim_text": "claim"}
            ],
        },
        baseline_candidates=[],
    )

    assert normalized["rule_atoms"][0]["chapter"] == "attributes"
