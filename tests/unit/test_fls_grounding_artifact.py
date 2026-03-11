from __future__ import annotations

from pathlib import Path

from scripts.retrieval.writer_host.fls_grounding import build_grounding_artifact


def test_build_grounding_artifact_preserves_decisive_phrases_without_runtime_hints() -> None:
    row = {
        "draft": {
            "title": "Prefer Strict Provenance APIs over integer-to-pointer reconstruction",
            "construct_terms": ["strict provenance", "pointer", "transmute"],
            "claim_to_evidence_map": [
                {
                    "claim_text": (
                        "Address-to-pointer casts require documented handling and "
                        "pointer-to-pointer transmute remains distinct."
                    )
                }
            ],
            "metadata": {"tags": ["irrelevant-tag", "ws7"]},
        },
        "amplification": {
            "guideline_amplification_text": (
                "Use Strict Provenance APIs instead of exposed-provenance integer-to-pointer "
                "reconstruction."
            )
        },
        "rationale": {
            "rationale_text": "Keep pointer provenance explicit rather than inferred from integer bits."
        },
        "examples": {
            "non_compliant_code": "let raw = addr as *const u8;",
            "compliant_code": "let raw = core::ptr::addr_of!(value);",
        },
        "debug_label": "ignored-grounding-noise",
    }

    artifact = build_grounding_artifact(row, db_path=Path("/definitely/missing/fls.db"))

    assert artifact["governing_obligation"] == (
        "Use Strict Provenance APIs instead of exposed-provenance integer-to-pointer reconstruction."
    )
    assert (
        "Address-to-pointer casts require documented handling and pointer-to-pointer transmute remains distinct."
        in artifact["supporting_phrases"]
    )
    assert (
        "Keep pointer provenance explicit rather than inferred from integer bits."
        in artifact["supporting_phrases"]
    )
    assert "irrelevant-tag" not in artifact["construct_terms"]
    assert artifact["ambiguity_notes"] == ["broad_document_priors", "broad_section_priors"]


def test_build_grounding_artifact_ignores_family_labels_and_tags_as_inputs() -> None:
    row = {
        "draft": {
            "title": "Spell the ABI explicitly on every extern block",
            "construct_terms": [],
            "claim_to_evidence_map": [
                {"claim_text": "Unsafe extern blocks require explicit ABI spelling."}
            ],
            "metadata": {"tags": ["ffi", "extern-family"]},
        },
        "amplification": {"guideline_amplification_text": ""},
        "rationale": {"rationale_text": ""},
        "examples": {
            "non_compliant_code": "extern { fn f(); }",
            "compliant_code": 'extern "C" { fn f(); }',
        },
        "target_id": "gui_deadbeef",
        "debug_label": "ignored-grounding-noise",
    }

    artifact = build_grounding_artifact(row, db_path=Path("/definitely/missing/fls.db"))

    assert artifact["construct_terms"][:6] == [
        "spell",
        "abi",
        "explicitly",
        "every",
        "extern",
        "block",
    ]
    assert artifact["supporting_phrases"] == [
        "Spell the ABI explicitly on every extern block",
        "Unsafe extern blocks require explicit ABI spelling.",
    ]
    assert "ffi" not in artifact["construct_terms"]
    assert "extern-family" not in artifact["construct_terms"]
    assert "unsafe-extern" not in artifact["construct_terms"]
