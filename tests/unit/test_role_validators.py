from __future__ import annotations

from validation.role_validators import validate_role_output


def _spec(policy: str = "renderer_injected") -> dict:
    return {
        "citation_placement_policy": policy,
        "title_convention": {"examples": ["Use checked arithmetic for safety-critical math"]},
        "tag_convention": {"values_seen": ["atomics", "concurrency", "memory-ordering"]},
    }


def test_amplification_validator_catches_std_and_citation_errors() -> None:
    output = {
        "guideline_amplification_text": "Use `AtomicBool` with clear ordering semantics.",
        "normative_strength": "MUST",
        "amplification_citation_keys": [],
    }
    violations = validate_role_output(
        "amplification_author",
        output,
        _spec("renderer_injected"),
        {"AtomicBool": "std::sync::atomic::AtomicBool"},
        ["std::sync::atomic::AtomicBool"],
        "CORE-CONC-003",
    )
    checks = {value.check for value in violations}
    assert "std_role_missing" in checks
    assert "citation_keys_empty" in checks
    assert "normative_strength_invalid" in checks


def test_amplification_validator_requires_exact_cite_pattern_for_llm_authored() -> None:
    output = {
        "guideline_amplification_text": "This sentence discusses citation behavior without role syntax.",
        "normative_strength": "shall",
    }
    violations = validate_role_output(
        "amplification_author",
        output,
        _spec("llm_authored"),
        {},
        [],
        "CORE-CONC-003",
    )
    checks = {value.check for value in violations}
    assert "cite_missing" in checks


def test_metadata_validator_catches_generic_title_and_tags() -> None:
    output = {
        "title": "Guideline for CORE-CONC-003",
        "tags": ["core_docs", "table1-1"],
        "bibliography_rows": [{"citation_key": ""}],
        "category": "mandatory",
    }
    violations = validate_role_output(
        "metadata_citation_curator",
        output,
        _spec(),
        {},
        [],
        "CORE-CONC-003",
    )
    checks = {value.check for value in violations}
    severities = {value.check: value.severity for value in violations}
    assert "title_generic" in checks
    assert "tag_pipeline_internal" in checks
    assert "tag_iso_reference" in checks
    assert "bibliography_missing_key" in checks
    assert severities["category_mandatory"] == "warning"


def test_example_validator_catches_empty_and_missing_construct() -> None:
    output = {
        "non_compliant_code": "unsafe { do_thing(); }",
        "compliant_code": "",
        "non_compliant_miri_intent": "none",
        "non_compliant_narrative": "Bad pattern.",
        "compliant_narrative": "Good pattern.",
    }
    violations = validate_role_output(
        "example_author",
        output,
        _spec(),
        {},
        ["std::sync::atomic::AtomicBool"],
        "CORE-CONC-003",
    )
    checks = {value.check for value in violations}
    assert "compliant_code_empty" in checks
    assert "miri_intent_missing" in checks
    assert "example_not_construct_specific" in checks


def test_known_good_outputs_pass_error_checks() -> None:
    amplification = {
        "guideline_amplification_text": (
            "Use :std:`std::sync::atomic::AtomicBool` for shared flags with explicit ordering."
        ),
        "normative_strength": "shall",
        "amplification_citation_keys": ["CORE-CONC-003:SRC-1"],
    }
    metadata = {
        "title": "Use explicit atomic ordering for shared state transitions.",
        "tags": ["atomics", "memory-ordering"],
        "bibliography_rows": [{"citation_key": "CORE-CONC-003:SRC-1"}],
        "category": "required",
    }
    example = {
        "non_compliant_code": "let old = flag.load(Ordering::Relaxed);",
        "compliant_code": "let old = flag.load(Ordering::Acquire);",
        "non_compliant_narrative": "AtomicBool with weak ordering can hide updates.",
        "compliant_narrative": "AtomicBool with acquire/release preserves ordering intent.",
        "non_compliant_miri_intent": "check",
    }
    spec = _spec("renderer_injected")
    std_lookup = {"AtomicBool": "std::sync::atomic::AtomicBool"}
    construct_terms = ["std::sync::atomic::AtomicBool"]

    amp_violations = validate_role_output(
        "amplification_author",
        amplification,
        spec,
        std_lookup,
        construct_terms,
        "CORE-CONC-003",
    )
    meta_violations = validate_role_output(
        "metadata_citation_curator",
        metadata,
        spec,
        std_lookup,
        construct_terms,
        "CORE-CONC-003",
    )
    ex_violations = validate_role_output(
        "example_author",
        example,
        spec,
        std_lookup,
        construct_terms,
        "CORE-CONC-003",
    )

    assert [value for value in amp_violations if value.severity == "error"] == []
    assert [value for value in meta_violations if value.severity == "error"] == []
    assert [value for value in ex_violations if value.severity == "error"] == []
