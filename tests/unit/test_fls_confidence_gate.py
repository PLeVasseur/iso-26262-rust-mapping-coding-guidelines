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
