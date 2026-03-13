from __future__ import annotations

from retrieval.writer_host.validation import validate_role_output


def _contract() -> dict:
    return {
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


def test_example_author_requires_miri_intents() -> None:
    output = {
        "target_id": "RET-ISSUE-001",
        "non_compliant_narrative": "n",
        "non_compliant_code": "unsafe { core::ptr::read_volatile(&0u8); }",
        "compliant_narrative": "n",
        "compliant_code": "unsafe { core::ptr::read_volatile(&0u8); }",
        "example_citation_keys": ["CIT-1"],
        "non_compliant_miri_intent": "",
        "compliant_miri_intent": "",
    }
    violations = validate_role_output(
        role_name="example_author",
        output=output,
        role_contract=_contract(),
        evidence_ids=set(),
    )
    assert "non_compliant_miri_intent_invalid" in violations
    assert "compliant_miri_intent_invalid" in violations


def test_skip_requires_justification() -> None:
    output = {
        "target_id": "RET-ISSUE-001",
        "non_compliant_narrative": "n",
        "non_compliant_code": "unsafe { core::ptr::read_volatile(&0u8); }",
        "compliant_narrative": "n",
        "compliant_code": "fn ok() {}",
        "example_citation_keys": ["CIT-1"],
        "non_compliant_miri_intent": "skip",
        "compliant_miri_intent": "check",
    }
    violations = validate_role_output(
        role_name="example_author",
        output=output,
        role_contract=_contract(),
        evidence_ids=set(),
    )
    assert "non_compliant_miri_skip_requires_justification" in violations


def test_skip_with_canonical_justification_passes() -> None:
    output = {
        "target_id": "RET-ISSUE-001",
        "non_compliant_narrative": "n",
        "non_compliant_code": "unsafe { core::ptr::read_volatile(&0u8); }",
        "compliant_narrative": "n",
        "compliant_code": "fn ok() {}",
        "example_citation_keys": ["CIT-1"],
        "non_compliant_miri_intent": "skip",
        "compliant_miri_intent": "check",
        "non_compliant_miri_skip_justification": "requires target-specific environment",
    }
    violations = validate_role_output(
        role_name="example_author",
        output=output,
        role_contract=_contract(),
        evidence_ids=set(),
    )
    assert "non_compliant_miri_skip_requires_justification" not in violations


def test_alias_only_skip_justification_is_rejected_by_validator() -> None:
    output = {
        "target_id": "RET-ISSUE-001",
        "non_compliant_narrative": "n",
        "non_compliant_code": "unsafe { core::ptr::read_volatile(&0u8); }",
        "compliant_narrative": "n",
        "compliant_code": "fn ok() {}",
        "example_citation_keys": ["CIT-1"],
        "non_compliant_miri_intent": "skip",
        "compliant_miri_intent": "check",
        "non_compliant_miri_justification": "old alias",
    }
    violations = validate_role_output(
        role_name="example_author",
        output=output,
        role_contract=_contract(),
        evidence_ids=set(),
    )
    assert "non_compliant_miri_skip_justification_alias_used" in violations
    assert "non_compliant_miri_skip_requires_justification" in violations
