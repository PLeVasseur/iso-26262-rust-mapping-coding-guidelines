from __future__ import annotations

from context import fls_lookup


def test_resolve_for_construct_is_grounding_only_in_ws6(monkeypatch) -> None:
    monkeypatch.setattr(fls_lookup, "_db_has_paragraphs", lambda _db: True)
    monkeypatch.setattr(
        fls_lookup,
        "resolve_ws7_guideline",
        lambda **kwargs: {
            "paragraph_id": "fls_unsafe003",
            "decision": {
                "reason_code": "ACCEPTED",
                "accepted": True,
                "publish_accept": True,
                "review_candidate": False,
                "grounding_only_runtime": False,
                "top_candidates": [],
            },
        },
    )

    result = fls_lookup.resolve_fls_for_construct(["unsafe", "pointer", "dereference"])

    assert result["paragraph_id"] == "fls_unsafe003"
    assert result["decision"]["reason_code"] == "ACCEPTED"
    assert result["decision"]["grounding_only_runtime"] is False
    assert result["decision"]["top_candidates"] == []


def test_resolve_for_construct_ignores_expected_domains_in_ws6(monkeypatch) -> None:
    monkeypatch.setattr(fls_lookup, "_db_has_paragraphs", lambda _db: True)
    monkeypatch.setattr(
        fls_lookup,
        "resolve_ws7_guideline",
        lambda **kwargs: {
            "paragraph_id": "fls_atomic002",
            "decision": {
                "reason_code": "ACCEPTED",
                "accepted": True,
                "publish_accept": True,
                "review_candidate": False,
                "grounding_only_runtime": False,
                "top_candidates": [],
            },
        },
    )

    base = fls_lookup.resolve_fls_for_construct(["atomic", "fence", "ordering"])
    with_domains = fls_lookup.resolve_fls_for_construct(
        ["atomic", "fence", "ordering"],
        expected_domains=["unsafe", "concurrency"],
    )

    assert with_domains == base


def test_resolve_for_guideline_delegates_to_ws7_runtime(monkeypatch) -> None:
    monkeypatch.setattr(fls_lookup, "_db_has_paragraphs", lambda _db: True)
    seen: dict[str, object] = {}

    def fake_ws7(**kwargs):
        seen.update(kwargs)
        return {
            "paragraph_id": "fls_unsafe003",
            "decision": {
                "reason_code": "ACCEPTED",
                "accepted": True,
                "publish_accept": True,
                "review_candidate": False,
                "grounding_only_runtime": False,
                "top_candidates": [],
            },
        }

    monkeypatch.setattr(fls_lookup, "resolve_ws7_guideline", fake_ws7)
    packet = {
        "governing_obligation": "Unsafe code must preserve invariants",
        "construct_terms": ["unsafe", "invariants"],
        "code_tokens": ["get_unchecked"],
        "supporting_phrases": ["fault paths must remain safe"],
        "prior_documents": [],
        "prior_sections": [],
        "ambiguity_notes": [],
    }

    result = fls_lookup.resolve_fls_for_guideline(
        packet,
        precomputed_candidates=[{"paragraph_id": "fls_unsafe003"}],
        precomputed_variants=[{"name": "packet_text", "query": "unsafe invariants"}],
    )

    assert result["paragraph_id"] == "fls_unsafe003"
    assert result["decision"]["reason_code"] == "ACCEPTED"
    assert seen["packet"] == packet


def test_resolve_requires_construct_terms(monkeypatch) -> None:
    monkeypatch.setattr(fls_lookup, "_db_has_paragraphs", lambda _db: True)

    result = fls_lookup.resolve_fls_for_guideline(
        {
            "governing_obligation": "",
            "construct_terms": [],
            "code_tokens": [],
            "supporting_phrases": [],
            "prior_documents": [],
            "prior_sections": [],
            "ambiguity_notes": [],
        }
    )

    assert result["paragraph_id"] == "fls_UNRESOLVED"
    assert result["unresolved_reason"] == "no construct terms provided"


def test_ws6_runtime_removed_legacy_candidate_helpers() -> None:
    assert not hasattr(fls_lookup, "_field_terms")
    assert not hasattr(fls_lookup, "_collect_packet_tokens")
    assert not hasattr(fls_lookup, "_score_candidates")
    assert not hasattr(fls_lookup, "_load_policy")
    assert not hasattr(fls_lookup, "_effective_policy")
    assert not hasattr(fls_lookup, "_policy_threshold")
