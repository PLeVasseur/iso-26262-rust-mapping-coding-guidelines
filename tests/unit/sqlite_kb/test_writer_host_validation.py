from __future__ import annotations

from retrieval.writer_host.validation import validate_target_bundle


def test_validate_target_bundle_allows_raw_synth_evidence_ids_from_author_roles() -> None:
    outputs = {
        "evidence_synthesizer": {
            "target_id": "RET-ISSUE-010",
            "prompt_id": "RET-ISSUE-010",
            "evidence_ids": [
                "rust_reference::chunk::cd00",
                "core_docs::chunk::3273",
                "core_docs::chunk::2856",
            ],
            "claim_to_evidence_map": [
                {
                    "claim_id": "RET-ISSUE-010::claim::1",
                    "claim_text": "claim",
                    "evidence_refs": [{"evidence_id": "core_docs::chunk::2856"}],
                }
            ],
        },
        "amplification_author": {
            "amplification_citation_keys": [
                "rust_reference::chunk::cd00",
                "core_docs::chunk::3273",
                "core_docs::chunk::2856",
            ]
        },
        "example_author": {
            "example_citation_keys": [
                "rust_reference::chunk::cd00",
                "core_docs::chunk::3273",
                "core_docs::chunk::2856",
            ]
        },
        "rationale_author": {
            "rationale_citation_keys": [
                "rust_reference::chunk::cd00",
                "core_docs::chunk::3273",
                "core_docs::chunk::2856",
            ]
        },
        "metadata_citation_curator": {
            "citation_key_map": {
                "rustref-unsafety-safe-subset-cd00": "rust_reference::chunk::cd00",
                "coredocs-ptr-safety-strict-provenance-3273": "core_docs::chunk::3273",
            }
        },
    }

    violations = validate_target_bundle(target_id="RET-ISSUE-010", outputs=outputs)
    assert not [item for item in violations if "missing_citation_map" in item]
