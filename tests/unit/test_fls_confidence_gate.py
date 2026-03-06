from __future__ import annotations

from context import fls_lookup


def test_resolve_accepts_high_confidence_candidate(monkeypatch) -> None:
    monkeypatch.setattr(fls_lookup, "_db_has_paragraphs", lambda _db: True)
    monkeypatch.setattr(fls_lookup, "_match_exemplar_override", lambda _terms: "")
    monkeypatch.setattr(fls_lookup, "validate_fls_id", lambda _id, spec_lock_path=None: True)
    monkeypatch.setattr(
        fls_lookup,
        "search_fls_paragraphs",
        lambda query, db_path=None, limit=5: [
            {
                "paragraph_id": "fls_unsafe003",
                "paragraph_number": "19:2",
                "chapter": "Unsafety",
                "section": "Raw Pointers",
                "text": "Raw pointer dereference may cause undefined behavior in unsafe code.",
                "lexical_score": 0.86,
            },
            {
                "paragraph_id": "fls_expr999",
                "paragraph_number": "6.1:1",
                "chapter": "Expressions",
                "section": "Operator Expressions",
                "text": "Expression evaluation follows deterministic order rules.",
                "lexical_score": 0.92,
            },
        ],
    )

    result = fls_lookup.resolve_fls_for_construct(
        ["unsafe", "pointer", "dereference", "undefined", "behavior"],
        expected_domains=["unsafe"],
    )

    assert result["paragraph_id"] == "fls_unsafe003"
    assert result["decision"]["accepted"] is True
    assert result["decision"]["reason_code"] == "ACCEPTED"
    assert result["decision"]["publish_accept"] is True
    assert result["decision"]["review_candidate"] is True


def test_resolve_rejects_chapter_mismatch_even_when_id_valid(monkeypatch) -> None:
    monkeypatch.setattr(fls_lookup, "_db_has_paragraphs", lambda _db: True)
    monkeypatch.setattr(fls_lookup, "_match_exemplar_override", lambda _terms: "")
    monkeypatch.setattr(fls_lookup, "validate_fls_id", lambda _id, spec_lock_path=None: True)
    monkeypatch.setattr(
        fls_lookup,
        "search_fls_paragraphs",
        lambda query, db_path=None, limit=5: [
            {
                "paragraph_id": "fls_expr111",
                "paragraph_number": "6.5.6:31",
                "chapter": "Expressions",
                "section": "Operator Expressions",
                "text": "Unsafe code can panic when operator assumptions are violated.",
                "lexical_score": 0.95,
            }
        ],
    )

    result = fls_lookup.resolve_fls_for_construct(
        ["unsafe", "code", "undefined", "behavior"],
        expected_domains=["unsafe"],
    )

    assert result["paragraph_id"] == "fls_UNRESOLVED"
    assert result["decision"]["reason_code"] == "CHAPTER_MISMATCH"
    assert result["decision"]["publish_accept"] is False


def test_resolve_rejects_low_confidence_margin(monkeypatch) -> None:
    monkeypatch.setattr(fls_lookup, "_db_has_paragraphs", lambda _db: True)
    monkeypatch.setattr(fls_lookup, "_match_exemplar_override", lambda _terms: "")
    monkeypatch.setattr(fls_lookup, "validate_fls_id", lambda _id, spec_lock_path=None: True)
    monkeypatch.setattr(
        fls_lookup,
        "search_fls_paragraphs",
        lambda query, db_path=None, limit=5: [
            {
                "paragraph_id": "fls_a",
                "paragraph_number": "17:1",
                "chapter": "Concurrency",
                "section": "Atomics",
                "text": "Atomic fence ordering controls visibility.",
                "lexical_score": 0.80,
            },
            {
                "paragraph_id": "fls_b",
                "paragraph_number": "17:2",
                "chapter": "Concurrency",
                "section": "Atomics",
                "text": "Atomic fence ordering controls thread visibility.",
                "lexical_score": 0.80,
            },
        ],
    )

    result = fls_lookup.resolve_fls_for_construct(
        ["atomic", "fence", "ordering"],
        expected_domains=["concurrency"],
    )

    assert result["paragraph_id"] == "fls_UNRESOLVED"
    assert result["decision"]["reason_code"] == "LOW_CONFIDENCE_MARGIN"
    assert result["decision"]["review_candidate"] is True


def test_multifield_resolution_uses_variant_coverage_and_accepts(monkeypatch) -> None:
    monkeypatch.setattr(fls_lookup, "_db_has_paragraphs", lambda _db: True)
    monkeypatch.setattr(fls_lookup, "_match_exemplar_override", lambda _terms: "")
    monkeypatch.setattr(fls_lookup, "validate_fls_id", lambda _id, spec_lock_path=None: True)

    packet = {
        "title": "Weak defensive handling can reach unsafe UB paths",
        "construct_terms": ["unsafe", "invariants", "ub"],
        "expected_domains": ["unsafe"],
        "amplification_text": "Fault paths must preserve memory safety invariants.",
        "rationale_text": "Unchecked indexing in unsafe code can cause UB.",
        "non_compliant_narrative": "continues after parse errors",
        "non_compliant_code": "unsafe { *values.get_unchecked(idx) }",
        "compliant_narrative": "returns Result when invalid",
        "compliant_code": "values.get(idx)",
        "claim_phrases": ["safe callers must not trigger undefined behavior"],
    }
    candidates = [
        {
            "paragraph_id": "fls_unsafe003",
            "chapter": "Unsafety",
            "section": "Raw Pointers",
            "paragraph_number": "19:2",
            "text": (
                "Unsafe code must preserve invariants; raw pointer dereference "
                "may cause undefined behavior in unsafe paths."
            ),
            "lexical_score": 0.95,
            "variant_name": "title_focus",
        },
        {
            "paragraph_id": "fls_unsafe003",
            "chapter": "Unsafety",
            "section": "Raw Pointers",
            "paragraph_number": "19:2",
            "text": (
                "Unsafe code must preserve invariants; raw pointer dereference "
                "may cause undefined behavior in unsafe paths."
            ),
            "lexical_score": 0.96,
            "variant_name": "unsafe_code_focus",
        },
        {
            "paragraph_id": "fls_other",
            "chapter": "Expressions",
            "section": "Operator Expressions",
            "paragraph_number": "6:1",
            "text": "Arithmetic expressions evaluate deterministically.",
            "lexical_score": 0.20,
            "variant_name": "title_focus",
        },
    ]
    variants = [
        {"name": "title_focus", "query": "unsafe invariants"},
        {"name": "unsafe_code_focus", "query": "get_unchecked undefined"},
    ]

    result = fls_lookup.resolve_fls_for_guideline(
        packet,
        precomputed_candidates=candidates,
        precomputed_variants=variants,
    )

    assert result["paragraph_id"] == "fls_unsafe003"
    assert result["decision"]["accepted"] is True
    assert result["decision"]["variant_count"] >= 2


def test_multifield_resolution_rejects_low_variant_coverage(monkeypatch) -> None:
    monkeypatch.setattr(fls_lookup, "_db_has_paragraphs", lambda _db: True)
    monkeypatch.setattr(fls_lookup, "_match_exemplar_override", lambda _terms: "")
    monkeypatch.setattr(fls_lookup, "validate_fls_id", lambda _id, spec_lock_path=None: True)

    packet = {
        "title": "Weak defensive handling can reach unsafe UB paths",
        "construct_terms": ["unsafe", "invariants", "ub"],
        "expected_domains": ["unsafe"],
        "amplification_text": "",
        "rationale_text": "",
        "non_compliant_narrative": "",
        "non_compliant_code": "unsafe { *ptr }",
        "compliant_narrative": "",
        "compliant_code": "",
        "claim_phrases": [],
    }
    candidates = [
        {
            "paragraph_id": "fls_unsafe003",
            "chapter": "Unsafety",
            "section": "Raw Pointers",
            "paragraph_number": "19:2",
            "text": (
                "Unsafe code must preserve invariants; raw pointer dereference "
                "may cause undefined behavior in unsafe paths."
            ),
            "lexical_score": 0.95,
            "variant_name": "title_focus",
        }
    ]
    variants = [
        {"name": "title_focus", "query": "unsafe invariants"},
        {"name": "rationale_focus", "query": "fault paths"},
    ]

    result = fls_lookup.resolve_fls_for_guideline(
        packet,
        precomputed_candidates=candidates,
        precomputed_variants=variants,
    )

    assert result["paragraph_id"] == "fls_UNRESOLVED"
    assert result["decision"]["reason_code"] == "INSUFFICIENT_VARIANT_COVERAGE"


def test_policy_overrides_can_relax_confidence_for_offline_calibration(monkeypatch) -> None:
    monkeypatch.setattr(fls_lookup, "_db_has_paragraphs", lambda _db: True)
    monkeypatch.setattr(fls_lookup, "_match_exemplar_override", lambda _terms: "")
    monkeypatch.setattr(fls_lookup, "validate_fls_id", lambda _id, spec_lock_path=None: True)

    packet = {
        "title": "Unsafe behavior on fallback",
        "construct_terms": ["unsafe", "fallback"],
        "expected_domains": ["unsafe"],
        "amplification_text": "",
        "rationale_text": "",
        "non_compliant_narrative": "",
        "non_compliant_code": "unsafe { *ptr }",
        "compliant_narrative": "",
        "compliant_code": "",
        "claim_phrases": [],
    }
    candidates = [
        {
            "paragraph_id": "fls_unsafe003",
            "chapter": "Unsafety",
            "section": "Raw Pointers",
            "paragraph_number": "19:2",
            "text": "Unsafe code may trigger undefined behavior.",
            "lexical_score": 0.35,
            "variant_name": "title_focus",
        },
        {
            "paragraph_id": "fls_unsafe003",
            "chapter": "Unsafety",
            "section": "Raw Pointers",
            "paragraph_number": "19:2",
            "text": "Unsafe code may trigger undefined behavior.",
            "lexical_score": 0.36,
            "variant_name": "hybrid_focus",
        },
    ]
    variants = [{"name": "title_focus", "query": "unsafe fallback"}]

    strict = fls_lookup.resolve_fls_for_guideline(
        packet,
        precomputed_candidates=candidates,
        precomputed_variants=variants,
        policy_overrides={"thresholds": {"min_confidence_score": 0.95}},
    )
    relaxed = fls_lookup.resolve_fls_for_guideline(
        packet,
        precomputed_candidates=candidates,
        precomputed_variants=variants,
        policy_overrides={"thresholds": {"min_confidence_score": 0.2}},
    )

    assert strict["paragraph_id"] == "fls_UNRESOLVED"
    assert relaxed["paragraph_id"] == "fls_unsafe003"
