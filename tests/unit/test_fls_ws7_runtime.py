from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from context import fls_ws7


def _row(
    paragraph_id: str,
    *,
    text: str,
    paragraph_link: str,
    document_link: str,
    section_link: str,
    defined_terms: list[dict[str, str]] | None = None,
    term_refs: list[dict[str, str]] | None = None,
    syntax_refs: list[dict[str, str]] | None = None,
    std_refs: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "chunk_uid": paragraph_id,
        "paragraph_id": paragraph_id,
        "paragraph_link": paragraph_link,
        "document_link": document_link,
        "section_link": section_link,
        "section_heading": section_link,
        "chapter": document_link.replace(".html", "").title(),
        "section": section_link,
        "paragraph_number": paragraph_id,
        "text": text,
        "chunk_text": text,
        "defined_terms_json": json.dumps(defined_terms or []),
        "term_refs_json": json.dumps(term_refs or []),
        "syntax_defs_json": json.dumps([]),
        "syntax_refs_json": json.dumps(syntax_refs or []),
        "std_refs_json": json.dumps(std_refs or []),
        "source_anchor": paragraph_link,
        "checksum": f"checksum-{paragraph_id}",
        "relevance_score": 1.0,
        "lexical_score": 1.0,
        "semantic_score": 1.0,
        "reranker_score": 1.0,
    }


def _fake_runtime(monkeypatch, rows_by_scope: dict[str, list[dict[str, Any]]]) -> None:
    def fake_execute_retrieval_query(**kwargs):
        allowed_ids = kwargs.get("allowed_statement_ids")
        mode = str(kwargs["mode"])
        if allowed_ids is None:
            scope_name = "global"
            scope = {"state": "global", "allowed_scope_ids": []}
        else:
            normalized = [str(value).strip() for value in allowed_ids if str(value).strip()]
            allowed_set = set(normalized)
            if not allowed_set:
                return {
                    "executed_mode": mode,
                    "rows": [],
                    "row_count": 0,
                    "scope": {
                        "state": "restricted_empty",
                        "allowed_scope_ids": [],
                    },
                }
            if allowed_set == {"fls_glossary001", "fls_norm001"}:
                scope_name = "section"
            elif allowed_set == {"fls_weak001"}:
                scope_name = "document"
            elif allowed_set == {"fls_weak001", "fls_weak002"}:
                scope_name = "section"
            else:
                scope_name = "document"
            scope = {
                "state": "restricted_subset",
                "allowed_scope_ids": sorted(allowed_set),
            }
        rows = [dict(row) for row in rows_by_scope.get(scope_name, [])]
        if allowed_ids is not None:
            allowed_set = {str(value).strip() for value in allowed_ids if str(value).strip()}
            rows = [row for row in rows if str(row.get("paragraph_id", "")) in allowed_set]
        return {
            "executed_mode": mode,
            "rows": rows,
            "row_count": len(rows),
            "scope": scope,
        }

    class FakeSemanticBackendConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(
        fls_ws7,
        "_load_runtime_components",
        lambda project_root: (fake_execute_retrieval_query, FakeSemanticBackendConfig),
    )
    monkeypatch.setattr(fls_ws7, "_build_query_text", lambda project_root, packet: "query text")
    monkeypatch.setattr(
        fls_ws7,
        "get_paragraph",
        lambda topology_index, paragraph_id: object() if paragraph_id.startswith("fls_") else None,
    )


def _base_runtime_settings() -> dict[str, Any]:
    return {
        "contract_path": Path("config/sqlite_query_contracts/fls_spec.yaml"),
        "query_log_root": Path(".cache/query_logs"),
        "rewrite_rules_path": Path("config/sqlite_query_rewrite/fls_spec_rewrite.yaml"),
        "top_k": 10,
        "candidate_limit": 200,
        "semantic_base_url": "http://127.0.0.1:8080",
        "semantic_embed_base_url": "http://127.0.0.1:8080",
        "semantic_rerank_base_url": "http://127.0.0.1:8081",
        "semantic_timeout_sec": 1.0,
        "semantic_retries": 0,
        "embed_model_id": "embed-model",
        "reranker_model_id": "rerank-model",
        "hybrid_fusion_method": "weighted-v2",
        "hybrid_candidate_policy": "v2",
        "hybrid_rerank_pool_size": 128,
        "hybrid_lexical_min": 24,
        "hybrid_semantic_min": 24,
        "hybrid_lexical_floor_count": 24,
        "hybrid_lexical_floor_share": 0.25,
        "hybrid_rrf_k": 60,
        "hybrid_rrf_window": 128,
    }


def _resolve(packet: dict[str, Any], *, monkeypatch, rows_by_scope, policy_overrides=None):
    _fake_runtime(monkeypatch, rows_by_scope)
    monkeypatch.setattr(
        fls_ws7,
        "paragraph_ids_for_section",
        lambda topology_index, section_link: ["fls_glossary001", "fls_norm001"]
        if "terms" in section_link
        else ["fls_weak001", "fls_weak002"]
        if "weak-section" in section_link
        else ["fls_weak001"],
    )
    monkeypatch.setattr(
        fls_ws7,
        "paragraph_ids_for_document",
        lambda topology_index, document_link: ["fls_weak001"] if "weak" in document_link else [],
    )
    return fls_ws7.resolve_guideline(
        project_root=Path("."),
        packet=packet,
        db_path=Path("fake.db"),
        runtime_settings=_base_runtime_settings(),
        topology_index={},
        policy_overrides=policy_overrides,
    )


def test_select_stage_winner_prefers_non_glossary_within_margin() -> None:
    stage_result = {
        "qualifying_candidates": [
            {
                "paragraph_id": "fls_glossary001",
                "total_score": 0.62,
                "glossary_candidate": True,
            },
            {
                "paragraph_id": "fls_atomic002",
                "total_score": 0.58,
                "glossary_candidate": False,
            },
        ]
    }
    policy = {
        "scoring": {"comparison_margin": 0.08},
        "gating": {
            "acceptance_total_score_min": 0.28,
            "acceptance_margin_min": 0.05,
            "review_total_score_min": 0.22,
        },
    }

    winner, decision = fls_ws7._select_stage_winner(stage_result, policy=policy)

    assert winner is not None
    assert winner["paragraph_id"] == "fls_atomic002"
    assert decision["accepted"] is True
    assert decision["glossary_override_applied"] is True


def test_select_stage_winner_returns_review_for_close_scores() -> None:
    stage_result = {
        "qualifying_candidates": [
            {
                "paragraph_id": "fls_atomic002",
                "total_score": 0.44,
                "glossary_candidate": False,
            },
            {
                "paragraph_id": "fls_sendsync001",
                "total_score": 0.41,
                "glossary_candidate": False,
            },
        ]
    }
    policy = {
        "scoring": {"comparison_margin": 0.08},
        "gating": {
            "acceptance_total_score_min": 0.28,
            "acceptance_margin_min": 0.05,
            "review_total_score_min": 0.22,
        },
    }

    winner, decision = fls_ws7._select_stage_winner(stage_result, policy=policy)

    assert winner is not None
    assert decision["accepted"] is False
    assert decision["review_candidate"] is True
    assert decision["reason_code"] == "AMBIGUOUS_TOP_CANDIDATES"


def test_build_query_text_uses_only_declared_inputs_and_preserves_phrases() -> None:
    packet = {
        "governing_obligation": "Prefer Strict Provenance APIs over integer-to-pointer reconstruction.",
        "construct_terms": ["strict provenance", "pointer", "transmute"],
        "supporting_phrases": [
            "address-to-pointer casts require documented handling",
            "pointer-to-pointer transmute remains distinct",
            "strict provenance should stay explicit",
            "do not drop later phrases",
        ],
        "code_tokens": ["addr_of", "transmute"],
        "prior_documents": [{"document_link": "casts.html", "score": 1.0}],
        "prior_sections": [{"section_link": "casts.html#type-cast-expressions", "score": 1.0}],
        "metadata": {"tags": ["ffi", "irrelevant-tag"]},
        "debug_label": "ignored-runtime-noise",
    }

    query_text = fls_ws7._build_query_text(Path("."), packet)

    assert "Prefer Strict Provenance APIs over integer-to-pointer reconstruction." in query_text
    assert "address-to-pointer casts require documented handling" in query_text
    assert "pointer-to-pointer transmute remains distinct" in query_text
    assert "do not drop later phrases" in query_text
    assert "addr_of transmute" in query_text
    assert "irrelevant-tag" not in query_text
    assert "ignored-runtime-noise" not in query_text


def test_build_query_text_adds_attribute_hints_from_existing_terms() -> None:
    packet = {
        "governing_obligation": "Use lint attributes deliberately.",
        "construct_terms": ["deny", "forbid", "must_use", "core::hint::must_use"],
        "supporting_phrases": ["Built-in attributes control diagnostics."],
        "code_tokens": [],
        "prior_documents": [],
        "prior_sections": [],
    }

    query_text = fls_ws7._build_query_text(Path("."), packet)

    assert "attribute deny" in query_text
    assert "attribute forbid" in query_text
    assert "attribute must_use" in query_text
    assert "attribute core" not in query_text


def test_code_evidence_score_matches_syntax_tokens_in_chunk_text() -> None:
    row = _row(
        "fls_attr001",
        text="Attribute deny.",
        paragraph_link="attributes.html#fls_attr001",
        document_link="attributes.html",
        section_link="attributes.html#lint-check-attributes",
    )

    score, matched = fls_ws7._code_evidence_score(
        row,
        packet_code_terms={"#[deny]", "core"},
    )

    assert score > 0.0
    assert matched == ["deny"]


def test_phrase_evidence_score_prefers_address_to_pointer_clause() -> None:
    packet = {
        "supporting_phrases": [
            "An integer shall not be converted to a pointer.",
            "Address-to-pointer casts require documented handling.",
        ]
    }
    expected = _row(
        "fls_expected",
        text="An operand of integer type and target type *const V perform address-to-pointer cast.",
        paragraph_link="expressions.html#fls_expected",
        document_link="expressions.html",
        section_link="expressions.html#type-cast-expressions",
    )
    sibling = _row(
        "fls_sibling",
        text="An operand of type *const T and a target integer type perform pointer-to-address cast.",
        paragraph_link="expressions.html#fls_sibling",
        document_link="expressions.html",
        section_link="expressions.html#type-cast-expressions",
    )

    expected_score = fls_ws7._phrase_evidence_score(expected, packet)
    sibling_score = fls_ws7._phrase_evidence_score(sibling, packet)

    assert expected_score["phrase_evidence_score"] > sibling_score["phrase_evidence_score"]


def test_project_query_inputs_strips_undeclared_runtime_fields() -> None:
    packet = {
        "governing_obligation": "unsafe extern blocks require explicit ABI",
        "construct_terms": ["unsafe", "extern", "abi"],
        "supporting_phrases": ["unsafe extern blocks require explicit ABI"],
        "code_tokens": ["extern", "C"],
        "prior_documents": [{"document_link": "ffi.html", "score": 1.0}],
        "prior_sections": [{"section_link": "ffi.html#extern-blocks", "score": 1.0}],
        "metadata": {"tags": ["ffi"]},
        "target_id": "gui_deadbeef",
    }

    projected = fls_ws7._project_query_inputs(packet)

    assert set(projected) == set(fls_ws7.DECLARED_QUERY_INPUT_FIELDS)
    assert projected["governing_obligation"] == packet["governing_obligation"]
    assert projected["construct_terms"] == packet["construct_terms"]


def test_resolve_guideline_keeps_glossary_visible_but_selects_normative_candidate(
    monkeypatch,
) -> None:
    packet = {
        "governing_obligation": "unsafe pointer dereference",
        "construct_terms": ["unsafe", "pointer", "dereference"],
        "code_tokens": [],
        "supporting_phrases": ["unsafe pointer"],
        "prior_documents": [],
        "prior_sections": [],
        "ambiguity_notes": [],
    }
    glossary = _row(
        "fls_glossary001",
        text="unsafe pointer dereference definition",
        paragraph_link="glossary.html#fls_glossary001",
        document_link="glossary.html",
        section_link="glossary.html#terms",
        defined_terms=[{"text": "unsafe pointer", "target": "unsafe pointer"}],
        term_refs=[{"text": "pointer", "target": "pointer"}],
    )
    normative = _row(
        "fls_norm001",
        text="unsafe pointer dereference is prohibited",
        paragraph_link="unsafety.html#fls_norm001",
        document_link="unsafety.html",
        section_link="unsafety.html#unsafe-pointer",
        term_refs=[{"text": "pointer", "target": "pointer"}],
    )

    resolved = _resolve(
        packet,
        monkeypatch=monkeypatch,
        rows_by_scope={"global": [glossary, normative]},
    )

    assert resolved["paragraph_id"] == "fls_norm001"
    assert resolved["decision"]["accepted"] is True
    assert resolved["decision"]["glossary_override_applied"] is True
    glossary_trace = next(
        row
        for row in resolved["decision"]["top_candidates"]
        if row["paragraph_id"] == "fls_glossary001"
    )
    assert glossary_trace["glossary_candidate"] is True
    assert glossary_trace["matched_role_features"]["defined_terms"]


def test_resolve_guideline_allows_glossary_win_when_no_normative_rival_qualifies(
    monkeypatch,
) -> None:
    packet = {
        "governing_obligation": "unsafe pointer dereference",
        "construct_terms": ["unsafe", "pointer", "dereference"],
        "code_tokens": [],
        "supporting_phrases": ["unsafe pointer dereference definition"],
        "prior_documents": [],
        "prior_sections": [],
        "ambiguity_notes": [],
    }
    glossary = _row(
        "fls_glossary001",
        text="unsafe pointer dereference definition",
        paragraph_link="glossary.html#fls_glossary001",
        document_link="glossary.html",
        section_link="glossary.html#terms",
        defined_terms=[{"text": "unsafe pointer", "target": "unsafe pointer"}],
        term_refs=[{"text": "pointer", "target": "pointer"}],
    )
    weak_normative = _row(
        "fls_norm001",
        text="unsafe",
        paragraph_link="unsafety.html#fls_norm001",
        document_link="unsafety.html",
        section_link="unsafety.html#unsafe-pointer",
    )

    resolved = _resolve(
        packet,
        monkeypatch=monkeypatch,
        rows_by_scope={"global": [glossary, weak_normative]},
    )

    assert resolved["paragraph_id"] == "fls_glossary001"
    assert resolved["decision"]["accepted"] is True
    assert resolved["decision"]["glossary_override_applied"] is False


def test_resolve_guideline_emits_review_for_ambiguous_runtime_case(monkeypatch) -> None:
    packet = {
        "governing_obligation": "atomic ordering visibility",
        "construct_terms": ["atomic", "ordering", "visibility"],
        "code_tokens": [],
        "supporting_phrases": ["thread visibility"],
        "prior_documents": [],
        "prior_sections": [],
        "ambiguity_notes": [],
    }
    rows = [
        _row(
            "fls_atomic001",
            text="atomic ordering visibility",
            paragraph_link="concurrency.html#fls_atomic001",
            document_link="concurrency.html",
            section_link="concurrency.html#atomics",
            term_refs=[{"text": "atomic", "target": "atomic"}],
        ),
        _row(
            "fls_atomic002",
            text="atomic ordering visibility",
            paragraph_link="concurrency.html#fls_atomic002",
            document_link="concurrency.html",
            section_link="concurrency.html#atomics",
            term_refs=[{"text": "atomic", "target": "atomic"}],
        ),
    ]

    resolved = _resolve(
        packet,
        monkeypatch=monkeypatch,
        rows_by_scope={"global": rows},
        policy_overrides={
            "gating": {
                "acceptance_total_score_min": 0.28,
                "acceptance_margin_min": 0.5,
                "review_total_score_min": 0.22,
            }
        },
    )

    assert resolved["paragraph_id"] in {"fls_atomic001", "fls_atomic002"}
    assert resolved["decision"]["accepted"] is False
    assert resolved["decision"]["review_candidate"] is True
    assert resolved["decision"]["reason_code"] == "AMBIGUOUS_TOP_CANDIDATES"


def test_resolve_guideline_broadens_when_retrieved_rows_do_not_qualify(monkeypatch) -> None:
    packet = {
        "governing_obligation": "atomic fence ordering visibility",
        "construct_terms": ["atomic", "fence", "ordering", "visibility"],
        "code_tokens": [],
        "supporting_phrases": ["visibility"],
        "prior_documents": [{"document_link": "weak.html", "score": 1.0}],
        "prior_sections": [{"section_link": "weak.html#weak-section", "score": 1.0}],
        "ambiguity_notes": [],
    }
    weak_row = _row(
        "fls_weak001",
        text="atomic",
        paragraph_link="weak.html#fls_weak001",
        document_link="weak.html",
        section_link="weak.html#weak-section",
    )
    global_row = _row(
        "fls_atomic099",
        text="atomic fence ordering visibility",
        paragraph_link="concurrency.html#fls_atomic099",
        document_link="concurrency.html",
        section_link="concurrency.html#atomics",
    )

    resolved = _resolve(
        packet,
        monkeypatch=monkeypatch,
        rows_by_scope={
            "section": [weak_row],
            "document": [weak_row],
            "global": [global_row],
        },
        policy_overrides={
            "qualification": {"component_evidence_min": 0.3, "total_score_min": 0.24},
            "scoring": {
                "components": {
                    "document_prior_score": {"weight": 0.0},
                    "section_prior_score": {"weight": 0.0},
                }
            },
            "gating": {
                "acceptance_total_score_min": 0.28,
                "acceptance_margin_min": 0.05,
                "review_total_score_min": 0.22,
            },
        },
    )

    assert resolved["paragraph_id"] == "fls_atomic099"
    assert resolved["decision"]["selected_stage"] == "global"
    assert [
        stage["advancement_reason"] for stage in resolved["decision"]["stage_artifacts"][:2]
    ] == [
        "GLOBAL_FALLBACK_REQUIRED",
        "GLOBAL_FALLBACK_REQUIRED",
    ]


def test_run_stage_disables_query_rewrite_and_uses_declared_stage_scopes(monkeypatch) -> None:
    packet = {
        "governing_obligation": "atomic fence ordering visibility",
        "construct_terms": ["atomic", "fence", "ordering", "visibility"],
        "code_tokens": [],
        "supporting_phrases": ["visibility"],
        "prior_documents": [{"document_link": "weak.html", "score": 1.0}],
        "prior_sections": [{"section_link": "glossary.html#terms", "score": 1.0}],
    }
    calls: list[dict[str, Any]] = []

    def fake_execute_retrieval_query(**kwargs):
        calls.append(
            {
                "mode": kwargs["mode"],
                "rewrite_mode": kwargs["rewrite_mode"],
                "allowed_statement_ids": kwargs.get("allowed_statement_ids"),
            }
        )
        return {
            "executed_mode": kwargs["mode"],
            "rows": [],
            "row_count": 0,
            "scope": {"state": "restricted_empty", "allowed_scope_ids": []},
        }

    class FakeSemanticBackendConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(
        fls_ws7,
        "_load_runtime_components",
        lambda project_root: (fake_execute_retrieval_query, FakeSemanticBackendConfig),
    )
    monkeypatch.setattr(
        fls_ws7,
        "paragraph_ids_for_section",
        lambda topology_index, section_link: ["fls_glossary001"]
        if section_link == "glossary.html#terms"
        else [],
    )
    monkeypatch.setattr(
        fls_ws7,
        "paragraph_ids_for_document",
        lambda topology_index, document_link: ["fls_doc001"]
        if document_link == "weak.html"
        else [],
    )

    fls_ws7.resolve_guideline(
        project_root=Path("."),
        packet=packet,
        db_path=Path("fake.db"),
        runtime_settings=_base_runtime_settings(),
        topology_index={},
    )

    assert len(calls) == 9
    assert all(call["rewrite_mode"] == "off" for call in calls)
    assert calls[0]["allowed_statement_ids"] == ["fls_glossary001"]
    assert calls[3]["allowed_statement_ids"] == ["fls_doc001"]
    assert calls[6]["allowed_statement_ids"] is None


def test_merge_canonical_row_is_invariant_to_mode_order() -> None:
    lexical = _row(
        "fls_atomic002",
        text="",
        paragraph_link="concurrency.html#fls_atomic002",
        document_link="concurrency.html",
        section_link="concurrency.html#atomics",
    )
    hybrid = _row(
        "fls_atomic002",
        text="atomic fence ordering visibility",
        paragraph_link="concurrency.html#fls_atomic002",
        document_link="concurrency.html",
        section_link="concurrency.html#atomics",
        term_refs=[{"text": "atomic", "target": "atomic"}],
    )

    left, left_merge = fls_ws7._merge_canonical_row({"lexical": lexical, "hybrid": hybrid})
    right, right_merge = fls_ws7._merge_canonical_row({"hybrid": hybrid, "lexical": lexical})

    assert left == right
    assert left_merge == right_merge
    assert left["text"] == "atomic fence ordering visibility"


def test_merge_canonical_row_surfaces_identity_conflict() -> None:
    lexical = _row(
        "fls_atomic002",
        text="atomic ordering visibility",
        paragraph_link="concurrency.html#fls_atomic002",
        document_link="concurrency.html",
        section_link="concurrency.html#atomics",
    )
    hybrid = dict(lexical)
    hybrid["document_link"] = "alt_concurrency.html"

    canonical, merge = fls_ws7._merge_canonical_row({"lexical": lexical, "hybrid": hybrid})

    assert canonical["document_link"] == "alt_concurrency.html"
    assert merge["identity_conflicts"]["document_link"] == [
        "alt_concurrency.html",
        "concurrency.html",
    ]


def test_merge_canonical_row_surfaces_ranking_conflict_deterministically() -> None:
    lexical = _row(
        "fls_atomic002",
        text="short text",
        paragraph_link="concurrency.html#fls_atomic002",
        document_link="concurrency.html",
        section_link="concurrency.html#atomics",
    )
    hybrid = _row(
        "fls_atomic002",
        text="atomic fence ordering visibility with stronger evidence",
        paragraph_link="concurrency.html#fls_atomic002",
        document_link="concurrency.html",
        section_link="concurrency.html#atomics",
        term_refs=[{"text": "atomic", "target": "atomic"}],
    )

    canonical, merge = fls_ws7._merge_canonical_row({"lexical": lexical, "hybrid": hybrid})

    assert canonical["text"] == hybrid["text"]
    assert merge["selected_modes"]["text"] == "hybrid"
    assert merge["ranking_conflicts"]["text"] == [
        "atomic fence ordering visibility with stronger evidence",
        "short text",
    ]


def test_resolve_guideline_identity_conflict_prevents_qualification(monkeypatch) -> None:
    packet = {
        "governing_obligation": "atomic ordering visibility",
        "construct_terms": ["atomic", "ordering", "visibility"],
        "code_tokens": [],
        "supporting_phrases": ["thread visibility"],
        "prior_documents": [],
        "prior_sections": [],
        "ambiguity_notes": [],
    }
    lexical = _row(
        "fls_atomic002",
        text="atomic ordering visibility",
        paragraph_link="concurrency.html#fls_atomic002",
        document_link="concurrency.html",
        section_link="concurrency.html#atomics",
    )
    hybrid = dict(lexical)
    hybrid["document_link"] = "alt_concurrency.html"

    def fake_execute_retrieval_query(**kwargs):
        mode = str(kwargs["mode"])
        rows = [lexical] if mode == "lexical" else [hybrid] if mode == "hybrid" else []
        return {
            "executed_mode": mode,
            "rows": [dict(row) for row in rows],
            "row_count": len(rows),
            "scope": {"state": "global", "allowed_scope_ids": []},
        }

    class FakeSemanticBackendConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(
        fls_ws7,
        "_load_runtime_components",
        lambda project_root: (fake_execute_retrieval_query, FakeSemanticBackendConfig),
    )
    monkeypatch.setattr(fls_ws7, "_build_query_text", lambda project_root, packet: "query text")
    monkeypatch.setattr(
        fls_ws7,
        "get_paragraph",
        lambda topology_index, paragraph_id: object() if paragraph_id.startswith("fls_") else None,
    )

    resolved = fls_ws7.resolve_guideline(
        project_root=Path("."),
        packet=packet,
        db_path=Path("fake.db"),
        runtime_settings=_base_runtime_settings(),
        topology_index={},
    )

    assert resolved["paragraph_id"] == "fls_UNRESOLVED"
    assert resolved["decision"]["reason_code"] == "NO_QUALIFYING_CANDIDATES"


def test_resolve_guideline_validation_only_continuation_collects_all_stages(monkeypatch) -> None:
    packet = {
        "governing_obligation": "unsafe pointer dereference",
        "construct_terms": ["unsafe", "pointer", "dereference"],
        "code_tokens": [],
        "supporting_phrases": ["unsafe pointer"],
        "prior_documents": [
            {
                "document_link": "glossary.html",
                "score": 1.0,
                "content_type": "glossary",
                "specificity_state": "glossary_dominated",
                "evidence": {},
            }
        ],
        "prior_sections": [
            {
                "section_link": "glossary.html#terms",
                "score": 1.0,
                "content_type": "glossary",
                "specificity_state": "glossary_dominated",
                "evidence": {},
            }
        ],
        "ambiguity_notes": [],
    }
    glossary = _row(
        "fls_glossary001",
        text="unsafe pointer definition",
        paragraph_link="glossary.html#fls_glossary001",
        document_link="glossary.html",
        section_link="glossary.html#terms",
        defined_terms=[{"text": "unsafe pointer", "target": "unsafe pointer"}],
    )

    resolved = _resolve(
        packet,
        monkeypatch=monkeypatch,
        rows_by_scope={"section": [glossary], "document": [glossary], "global": [glossary]},
        policy_overrides={"validation_only_continuation": True},
    )

    assert resolved["decision"]["selected_stage"] == "section"
    assert [stage["stage_name"] for stage in resolved["decision"]["stage_artifacts"]] == [
        "section",
        "document",
        "global",
    ]
    assert (
        resolved["decision"]["stage_artifacts"][0]["advancement_reason"]
        == "VALIDATION_ONLY_CONTINUATION"
    )


def test_resolve_guideline_is_reproducible_for_same_input(monkeypatch) -> None:
    packet = {
        "governing_obligation": "atomic ordering visibility",
        "construct_terms": ["atomic", "ordering", "visibility"],
        "code_tokens": [],
        "supporting_phrases": ["thread visibility"],
        "prior_documents": [],
        "prior_sections": [],
        "ambiguity_notes": [],
    }
    rows = [
        _row(
            "fls_atomic002",
            text="atomic ordering visibility",
            paragraph_link="concurrency.html#fls_atomic002",
            document_link="concurrency.html",
            section_link="concurrency.html#atomics",
        )
    ]

    first = _resolve(packet, monkeypatch=monkeypatch, rows_by_scope={"global": rows})
    second = _resolve(packet, monkeypatch=monkeypatch, rows_by_scope={"global": rows})

    assert first == second
