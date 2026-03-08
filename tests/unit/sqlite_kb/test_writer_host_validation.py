from __future__ import annotations

import sys
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.validation import validate_role_output, validate_target_bundle


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


def test_validate_example_author_flags_compile_fail_and_deprecated_patterns() -> None:
    contract = {
        "required_output_schema": {
            "required": [
                "target_id",
                "non_compliant_narrative",
                "non_compliant_code",
                "compliant_narrative",
                "compliant_code",
                "example_citation_keys",
                "non_compliant_miri_intent",
                "compliant_miri_intent",
            ]
        },
        "forbidden_patterns": [],
    }
    output = {
        "target_id": "RET-RESOLVE-008",
        "non_compliant_narrative": "bad",
        "non_compliant_code": 'match sample { &0..=5 => "low", _ => "other" }',
        "compliant_narrative": "good",
        "compliant_code": "unsafe { s.slice_unchecked(start, s.len()) }",
        "example_citation_keys": ["rust_reference::chunk::e42c"],
        "non_compliant_miri_intent": "skip",
        "non_compliant_miri_skip_justification": "The example is intentionally ill-formed Rust.",
        "compliant_miri_intent": "check",
    }

    violations = validate_role_output(
        role_name="example_author",
        output=output,
        role_contract=contract,
        evidence_ids={"rust_reference::chunk::e42c"},
    )

    assert "non_compliant_code:compile_fail_pattern_ambiguous_ref_range" in violations
    assert "compliant_code:deprecated_api:slice_unchecked" in violations
    assert "compile_fail_examples_not_allowed_by_default" in violations


def test_validate_example_author_flags_unstable_and_dead_code_probe_issues() -> None:
    contract = {
        "required_output_schema": {
            "required": [
                "target_id",
                "non_compliant_narrative",
                "non_compliant_code",
                "compliant_narrative",
                "compliant_code",
                "example_citation_keys",
                "non_compliant_miri_intent",
                "compliant_miri_intent",
            ]
        },
        "forbidden_patterns": [],
    }
    output = {
        "target_id": "RET-ISSUE-006",
        "non_compliant_narrative": "bad",
        "non_compliant_code": (
            "fn helper() {}\nfn main() {\n    core::hint::must_use(helper());\n}\n"
        ),
        "compliant_narrative": "good",
        "compliant_code": ("trait ProcessingRole {\n    fn process() -> u32;\n}\nfn main() {}\n"),
        "example_citation_keys": ["rust_reference::chunk::e42c"],
        "non_compliant_miri_intent": "check",
        "compliant_miri_intent": "check",
    }

    violations = validate_role_output(
        role_name="example_author",
        output=output,
        role_contract=contract,
        evidence_ids={"rust_reference::chunk::e42c"},
    )

    assert "non_compliant_code:unstable_api:core_hint_must_use" in violations
    assert "non_compliant_code:rustc_probe:unstable_api" in violations
    assert "compliant_code:rustc_probe:dead_code_warning" in violations


def test_validate_example_author_probe_keeps_workspace_clean(tmp_path: Path) -> None:
    contract = {
        "required_output_schema": {
            "required": [
                "target_id",
                "non_compliant_narrative",
                "non_compliant_code",
                "compliant_narrative",
                "compliant_code",
                "example_citation_keys",
                "non_compliant_miri_intent",
                "compliant_miri_intent",
            ]
        },
        "forbidden_patterns": [],
    }
    output = {
        "target_id": "RET-ISSUE-006",
        "non_compliant_narrative": "bad",
        "non_compliant_code": (
            "fn helper() {}\nfn main() {\n    core::hint::must_use(helper());\n}\n"
        ),
        "compliant_narrative": "good",
        "compliant_code": ("trait ProcessingRole {\n    fn process() -> u32;\n}\nfn main() {}\n"),
        "example_citation_keys": ["rust_reference::chunk::e42c"],
        "non_compliant_miri_intent": "check",
        "compliant_miri_intent": "check",
    }
    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        violations = validate_role_output(
            role_name="example_author",
            output=output,
            role_contract=contract,
            evidence_ids={"rust_reference::chunk::e42c"},
        )
        assert "non_compliant_code:rustc_probe:unstable_api" in violations
        assert "compliant_code:rustc_probe:dead_code_warning" in violations
        assert not (tmp_path / "probe").exists()
        assert not list(tmp_path.glob("probe.long-type-*.txt"))
    finally:
        os.chdir(cwd)


def test_validate_metadata_curator_flags_inconsistent_duplicate_urls() -> None:
    contract = {
        "required_output_schema": {
            "required": [
                "target_id",
                "tags",
                "fls_candidate",
                "bibliography_rows",
                "citation_key_map",
                "metadata_validation_notes",
            ]
        },
        "forbidden_patterns": [],
    }
    output = {
        "target_id": "RET-ISSUE-002",
        "tags": ["strong-typing"],
        "fls_candidate": {"statement": "Strong typing"},
        "bibliography_rows": [
            {
                "citation_key": "RET-ISSUE-002:SRC-4",
                "title": "Rust Reference: `#[non_exhaustive]` marks structs, enums, and variants as extensible",
                "source_anchor": "https://doc.rust-lang.org/reference/attributes/type_system.html#the-non-exhaustive-attribute",
            },
            {
                "citation_key": "RET-ISSUE-002:SRC-5",
                "title": "Rust Reference: downstream construction and matching restrictions for `#[non_exhaustive]` preserve compatibility when fields or variants are added",
                "source_anchor": "https://doc.rust-lang.org/reference/attributes/type_system.html#the-non-exhaustive-attribute",
            },
        ],
        "citation_key_map": {"RET-ISSUE-002:SRC-4": "rust_reference::chunk::1"},
        "metadata_validation_notes": ["note"],
    }

    violations = validate_role_output(
        role_name="metadata_citation_curator",
        output=output,
        role_contract=contract,
        evidence_ids={"rust_reference::chunk::1"},
    )

    assert "bibliography_duplicate_url_inconsistent:1" in violations


def test_validate_metadata_curator_flags_exact_duplicate_rows() -> None:
    contract = {
        "required_output_schema": {
            "required": [
                "target_id",
                "tags",
                "fls_candidate",
                "bibliography_rows",
                "citation_key_map",
                "metadata_validation_notes",
            ]
        },
        "forbidden_patterns": [],
    }
    output = {
        "target_id": "RET-ISSUE-006",
        "tags": ["diagnostics"],
        "fls_candidate": {"statement": "Lint policy"},
        "bibliography_rows": [
            {
                "citation_key": "RET-ISSUE-006:SRC-1",
                "document": "Rust Reference",
                "title": "Diagnostic attributes",
                "source_anchor": "https://doc.rust-lang.org/reference/attributes/diagnostics.html#diagnostic-attributes",
            },
            {
                "citation_key": "RET-ISSUE-006:SRC-2",
                "document": "Rust Reference",
                "title": "Diagnostic attributes",
                "source_anchor": "https://doc.rust-lang.org/reference/attributes/diagnostics.html#diagnostic-attributes",
            },
        ],
        "citation_key_map": {"RET-ISSUE-006:SRC-1": "rust_reference::chunk::1"},
        "metadata_validation_notes": ["note"],
    }

    violations = validate_role_output(
        role_name="metadata_citation_curator",
        output=output,
        role_contract=contract,
        evidence_ids={"rust_reference::chunk::1"},
    )

    assert "bibliography_exact_duplicate:1" in violations
