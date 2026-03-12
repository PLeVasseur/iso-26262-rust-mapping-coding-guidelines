from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_fls_ws7  # noqa: E402


def _stage_artifact(
    *, paragraph_id: str = "fls_atomic002", advancement_reason: str = "TERMINAL_STAGE_SUCCESS"
):
    return {
        "stage_name": "global",
        "mode_artifacts": {
            mode: {
                "requested_mode": mode,
                "executed_mode": mode,
                "returned_candidate_count": 1,
                "qualifying_candidate_count": 1,
                "retrieval_result_ref": {
                    "stage": "global",
                    "query_text": "atomic ordering",
                    "scope": {"state": "global"},
                    "qualifying_paragraph_ids": [paragraph_id],
                    "rows": [
                        {
                            "chunk_uid": paragraph_id,
                            "paragraph_id": paragraph_id,
                            "paragraph_link": f"concurrency.html#{paragraph_id}",
                        }
                    ],
                },
            }
            for mode in ("lexical", "semantic", "hybrid")
        },
        "candidate_universe_size": -1,
        "advancement_reason": advancement_reason,
        "candidate_ids": [
            {
                "chunk_uid": paragraph_id,
                "paragraph_id": paragraph_id,
                "paragraph_link": f"concurrency.html#{paragraph_id}",
                "first_seen_stage": "global",
                "seen_in_modes": ["lexical", "semantic", "hybrid"],
                "mode_row_refs": {
                    mode: {
                        "rank": 1,
                        "stage": "global",
                        "chunk_uid": paragraph_id,
                        "paragraph_id": paragraph_id,
                    }
                    for mode in ("lexical", "semantic", "hybrid")
                },
            }
        ],
    }


def _top_candidate(*, paragraph_id: str = "fls_atomic002"):
    return {
        "chunk_uid": paragraph_id,
        "paragraph_id": paragraph_id,
        "paragraph_link": f"concurrency.html#{paragraph_id}",
        "retrieval_stage": "global",
        "first_seen_stage": "global",
        "seen_in_modes": ["lexical", "semantic", "hybrid"],
        "mode_row_refs": {
            mode: {
                "rank": 1,
                "stage": "global",
                "chunk_uid": paragraph_id,
                "paragraph_id": paragraph_id,
            }
            for mode in ("lexical", "semantic", "hybrid")
        },
        "score_components": {
            "text_overlap_score": 1.0,
            "phrase_evidence_score": 1.0,
            "document_prior_score": 0.0,
            "section_prior_score": 0.0,
            "defined_term_match_score": 0.0,
            "term_ref_match_score": 0.0,
            "syntax_match_score": 0.0,
            "std_ref_match_score": 0.0,
            "code_evidence_score": 0.0,
            "glossary_terminal_penalty": 0.0,
            "ambiguity_penalty": 0.0,
        },
        "total_score": 0.9,
        "glossary_candidate": False,
        "matched_role_features": {},
        "canonical_merge": {
            "identity_conflicts": {},
            "selected_values": {"paragraph_id": paragraph_id},
        },
        "qualifying_candidate": True,
    }


def test_run_validation_reports_stage_artifacts(monkeypatch) -> None:
    monkeypatch.setattr(
        validate_fls_ws7,
        "load_calibration_items",
        lambda **kwargs: [
            {
                "path": "x.rst",
                "packet": {"construct_terms": ["atomic"]},
                "acceptable_ids": ["fls_atomic002"],
                "allow_review": False,
                "allow_unresolved": False,
            }
        ],
    )
    monkeypatch.setattr(
        validate_fls_ws7,
        "resolve_fls_for_guideline",
        lambda packet: {
            "paragraph_id": "fls_atomic002",
            "decision": {
                "accepted": True,
                "reason_code": "ACCEPTED",
                "selected_stage": "global",
                "review_candidate": False,
                "stage_artifacts": [_stage_artifact()],
                "top_candidates": [_top_candidate()],
            },
        },
    )

    report = validate_fls_ws7.run_validation()

    assert report["runtime_mode"] == "ws7_staged_retrieval_v1"
    assert report["dataset"]["source"] == "<exemplar_manifest>"
    assert report["accepted_correct"] == 1
    assert report["structural_failures"] == 0
    assert report["proof_valid"] is True
    assert report["rows"][0]["stage_sequence_entered"] == ["global"]
    assert report["rows"][0]["investigation_record"]["item_id"] == "x"
    assert (
        report["rows"][0]["proof_bundle"]["routing_artifact"]["kind"]
        == "scoped_candidate_universe_diff"
    )
    assert report["rows"][0]["proof_bundle"]["ranking_artifact"]["kind"] == "score_component_diff"
    assert (
        report["rows"][0]["proof_bundle"]["structural_artifact"]["kind"] == "validation_report_row"
    )
    assert report["rows"][0]["triage_classification"] == "expected_abstention"
    assert report["rows"][0]["runtime_queue"] is False


def test_build_targeted_family_report_aggregates_runtime_families(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir(parents=True)
    (reports_root / "ws7_targeted_batch_attribution_summary.json").write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "run_id": "v17_2_batch_a_writer_ws7",
                        "blocked_count": 2,
                        "reason_counts": {
                            "AMBIGUOUS_TOP_CANDIDATES": 1,
                            "NO_QUALIFYING_CANDIDATES": 1,
                        },
                        "status": "blocked",
                        "attribution": "ws7_retrieval_ranking_blockers_present",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    batch_dir = reports_root / "v17_2_batch_a_writer_ws7_final"
    batch_dir.mkdir()
    (batch_dir / "writer_review_admissibility_report.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "draft_id": "draft::a",
                        "atom_id": "A::atom::1",
                        "guideline_id": "gui_a",
                        "guideline_family_key": "diagnostics_policy",
                        "candidate_title": "Diagnose ignored required values",
                        "warning_reasons": ["fls_unresolved:AMBIGUOUS_TOP_CANDIDATES"],
                        "blocking_reasons": ["release_defaulted"],
                        "admissibility_status": "block",
                        "metadata_status": "block",
                        "taxonomy_status": "pass",
                    },
                    {
                        "draft_id": "draft::b",
                        "atom_id": "B::atom::1",
                        "guideline_id": "gui_b",
                        "guideline_family_key": "diagnostics_policy",
                        "candidate_title": "Lock safety lint levels",
                        "warning_reasons": ["fls_unresolved:NO_QUALIFYING_CANDIDATES"],
                        "blocking_reasons": [],
                        "admissibility_status": "admit",
                        "metadata_status": "review",
                        "taxonomy_status": "pass",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (batch_dir / "family_resolution_report.json").write_text(
        json.dumps(
            {
                "clusters": [
                    {
                        "cluster_id": "diagnostics_policy",
                        "cluster_kind": "family_duplicate",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = validate_fls_ws7.build_targeted_family_report(reports_root=reports_root)

    assert report["recommended_first_family"] == "diagnostics_policy"
    assert report["family_rows"][0]["family_name"] == "diagnostics_policy"
    assert report["family_rows"][0]["runtime_blockers"] == 2
    assert report["family_rows"][0]["mapping_blockers"] == 1
    assert report["item_rows"][0]["triage"] == "true_ranking_bug"


def test_build_targeted_family_report_marks_mapping_only_family(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    reports_root.mkdir(parents=True)
    (reports_root / "ws7_targeted_batch_attribution_summary.json").write_text(
        json.dumps({"runs": []}),
        encoding="utf-8",
    )

    report = validate_fls_ws7.build_targeted_family_report(reports_root=reports_root)

    assert report["family_rows"] == []
    assert report["item_rows"] == []


def test_build_family_trace_report_reruns_selected_family(monkeypatch, tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    batch_dir = reports_root / "v17_2_batch_f_writer_ws7"
    batch_dir.mkdir(parents=True)
    family_report = reports_root / "ws7_targeted_family_status.json"
    family_report.write_text(
        json.dumps(
            {
                "item_rows": [
                    {
                        "item_id": "RET-RESOLVE-006::atom::lint-levels",
                        "batch": "v17_2_batch_f_writer_ws7",
                        "family": "diagnostics_policy",
                        "non_runtime_blocker": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (batch_dir / "drafts.jsonl").write_text(
        json.dumps(
            {
                "atom_id": "RET-RESOLVE-006::atom::lint-levels",
                "title": "Lock safety lint levels",
                "construct_terms": ["#[deny]"],
                "claim_to_evidence_map": [{"claim_text": "Lint attributes can deny warnings."}],
                "review_question": "Are lint levels locked?",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def _resolve(packet, **kwargs):
        captured.update(kwargs)
        return {
            "paragraph_id": "fls_diag001",
            "decision": {
                "reason_code": "AMBIGUOUS_TOP_CANDIDATES",
                "selected_stage": "section",
                "stage_artifacts": [_stage_artifact(paragraph_id="fls_diag001")],
                "top_candidates": [_top_candidate(paragraph_id="fls_diag001")],
            },
        }

    monkeypatch.setattr(validate_fls_ws7, "resolve_fls_for_guideline", _resolve)

    report = validate_fls_ws7.build_family_trace_report(
        "diagnostics_policy",
        reports_root=reports_root,
        family_report_path=family_report,
    )

    assert report["family_name"] == "diagnostics_policy"
    assert report["item_count"] == 1
    assert report["runtime_profile"] == "debug"
    assert report["runtime_settings_overrides"]["candidate_limit"] == 12
    assert report["runtime_settings_overrides"]["ws7_modes"] == ["semantic"]
    assert report["rows"][0]["resolved_paragraph_id"] == "fls_diag001"
    assert report["rows"][0]["stage_sequence_entered"] == ["global"]
    assert "#[deny]" in report["rows"][0]["grounding_artifact_snapshot"]["code_tokens"]
    assert captured["runtime_settings_overrides"] == report["runtime_settings_overrides"]


def test_build_family_trace_report_filters_requested_item_ids(monkeypatch, tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    batch_dir = reports_root / "v17_2_batch_f_writer_ws7"
    batch_dir.mkdir(parents=True)
    family_report = reports_root / "ws7_targeted_family_status.json"
    family_report.write_text(
        json.dumps(
            {
                "item_rows": [
                    {
                        "item_id": "item-a",
                        "batch": "v17_2_batch_f_writer_ws7",
                        "family": "diagnostics_policy",
                        "non_runtime_blocker": False,
                    },
                    {
                        "item_id": "item-b",
                        "batch": "v17_2_batch_f_writer_ws7",
                        "family": "diagnostics_policy",
                        "non_runtime_blocker": False,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (batch_dir / "drafts.jsonl").write_text(
        json.dumps(
            {
                "atom_id": "item-a",
                "title": "A",
                "construct_terms": ["deny"],
                "claim_to_evidence_map": [{"claim_text": "A claim"}],
            }
        )
        + "\n"
        + json.dumps(
            {
                "atom_id": "item-b",
                "title": "B",
                "construct_terms": ["forbid"],
                "claim_to_evidence_map": [{"claim_text": "B claim"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        validate_fls_ws7,
        "resolve_fls_for_guideline",
        lambda packet, **kwargs: {
            "paragraph_id": "fls_diag001",
            "decision": {
                "reason_code": "AMBIGUOUS_TOP_CANDIDATES",
                "selected_stage": "section",
                "stage_artifacts": [_stage_artifact(paragraph_id="fls_diag001")],
                "top_candidates": [_top_candidate(paragraph_id="fls_diag001")],
            },
        },
    )

    report = validate_fls_ws7.build_family_trace_report(
        "diagnostics_policy",
        reports_root=reports_root,
        family_report_path=family_report,
        item_ids=["item-b"],
    )

    assert report["selected_item_ids"] == ["item-b"]
    assert report["item_count"] == 1
    assert report["rows"][0]["item_id"] == "item-b"


def test_runtime_profile_overrides_supports_debug_and_proof() -> None:
    assert validate_fls_ws7._runtime_profile_overrides("default") == {}
    assert validate_fls_ws7._runtime_profile_overrides("debug")["candidate_limit"] == 12
    assert validate_fls_ws7._runtime_profile_overrides("elevated_debug")["candidate_limit"] == 40
    assert validate_fls_ws7._runtime_profile_overrides("proof")["candidate_limit"] == 500


def test_write_validation_report_prefers_run_dir(tmp_path: Path) -> None:
    out = validate_fls_ws7.write_validation_report(
        {"runtime_mode": "ws7_staged_retrieval_v1"},
        run_dir=tmp_path,
    )

    assert out == tmp_path / "ws7_validation.json"
    assert json.loads(out.read_text(encoding="utf-8"))["runtime_mode"] == "ws7_staged_retrieval_v1"


def test_run_validation_reports_dataset_fingerprint(monkeypatch, tmp_path: Path) -> None:
    dataset = tmp_path / "heldout.json"
    dataset.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "frozen": True,
                "runtime_use_prohibited": False,
                "purpose": "heldout",
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(validate_fls_ws7, "load_calibration_items", lambda **kwargs: [])

    report = validate_fls_ws7.run_validation(dataset_path=dataset)

    assert report["dataset"]["source"] == str(dataset)
    assert report["dataset"]["frozen"] is True
    assert report["dataset"]["fingerprint_sha256"]


def test_run_validation_marks_wrong_accept_as_invalid(monkeypatch) -> None:
    monkeypatch.setattr(
        validate_fls_ws7,
        "load_calibration_items",
        lambda **kwargs: [
            {
                "path": "x.rst",
                "packet": {"construct_terms": ["atomic"]},
                "acceptable_ids": ["fls_atomic002"],
            }
        ],
    )
    monkeypatch.setattr(
        validate_fls_ws7,
        "resolve_fls_for_guideline",
        lambda packet: {
            "paragraph_id": "fls_wrong999",
            "decision": {
                "accepted": True,
                "review_candidate": False,
                "reason_code": "ACCEPTED",
                "selected_stage": "global",
                "stage_artifacts": [_stage_artifact(paragraph_id="fls_wrong999")],
                "top_candidates": [_top_candidate(paragraph_id="fls_wrong999")],
            },
        },
    )

    report = validate_fls_ws7.run_validation()

    assert report["accepted_wrong"] == 1
    assert report["proof_valid"] is False


def test_run_validation_marks_structural_problem_as_invalid(monkeypatch) -> None:
    monkeypatch.setattr(
        validate_fls_ws7,
        "load_calibration_items",
        lambda **kwargs: [
            {
                "path": "x.rst",
                "packet": {"construct_terms": ["atomic"]},
                "acceptable_ids": ["fls_atomic002"],
            }
        ],
    )
    monkeypatch.setattr(
        validate_fls_ws7,
        "resolve_fls_for_guideline",
        lambda packet: {
            "paragraph_id": "fls_atomic002",
            "decision": {
                "accepted": True,
                "review_candidate": False,
                "reason_code": "ACCEPTED",
                "selected_stage": "global",
                "stage_artifacts": [{"stage": "global"}],
                "top_candidates": [_top_candidate()],
            },
        },
    )

    report = validate_fls_ws7.run_validation()

    assert report["structural_failures"] == 1
    assert report["proof_valid"] is False


def test_run_validation_marks_unexpected_review_as_invalid(monkeypatch) -> None:
    monkeypatch.setattr(
        validate_fls_ws7,
        "load_calibration_items",
        lambda **kwargs: [
            {
                "path": "x.rst",
                "packet": {"construct_terms": ["atomic"]},
                "acceptable_ids": ["fls_atomic002"],
                "allow_review": False,
            }
        ],
    )
    monkeypatch.setattr(
        validate_fls_ws7,
        "resolve_fls_for_guideline",
        lambda packet: {
            "paragraph_id": "fls_atomic002",
            "decision": {
                "accepted": False,
                "review_candidate": True,
                "reason_code": "AMBIGUOUS_TOP_CANDIDATES",
                "selected_stage": "global",
                "stage_artifacts": [_stage_artifact()],
                "top_candidates": [_top_candidate()],
            },
        },
    )

    report = validate_fls_ws7.run_validation()

    assert report["review_unexpected"] == 1
    assert report["proof_valid"] is False


def test_run_validation_allows_expected_review(monkeypatch) -> None:
    monkeypatch.setattr(
        validate_fls_ws7,
        "load_calibration_items",
        lambda **kwargs: [
            {
                "path": "x.rst",
                "packet": {"construct_terms": ["atomic"]},
                "acceptable_ids": ["fls_atomic002"],
                "allow_review": True,
            }
        ],
    )
    monkeypatch.setattr(
        validate_fls_ws7,
        "resolve_fls_for_guideline",
        lambda packet: {
            "paragraph_id": "fls_atomic002",
            "decision": {
                "accepted": False,
                "review_candidate": True,
                "reason_code": "AMBIGUOUS_TOP_CANDIDATES",
                "selected_stage": "global",
                "stage_artifacts": [_stage_artifact()],
                "top_candidates": [_top_candidate()],
            },
        },
    )

    report = validate_fls_ws7.run_validation()

    assert report["review_correct"] == 1
    assert report["proof_valid"] is True
    assert report["investigation_records"][0]["failure_layer"] == "candidate_scoring_failure"
    assert report["rows"][0]["triage_classification"] == "expected_abstention"


def test_run_validation_marks_missing_scoped_entry_as_scope_failure(monkeypatch) -> None:
    stage_artifact = _stage_artifact()
    stage_artifact["stage_name"] = "section"
    monkeypatch.setattr(
        validate_fls_ws7,
        "load_calibration_items",
        lambda **kwargs: [
            {
                "path": "x.rst",
                "packet": {
                    "construct_terms": ["atomic"],
                    "prior_documents": [{"document_link": "weak.html", "score": 1.0}],
                    "prior_sections": [{"section_link": "weak.html#weak", "score": 1.0}],
                },
                "acceptable_ids": ["fls_expected"],
            }
        ],
    )
    monkeypatch.setattr(
        validate_fls_ws7,
        "resolve_fls_for_guideline",
        lambda packet: {
            "paragraph_id": "fls_wrong999",
            "decision": {
                "accepted": True,
                "review_candidate": False,
                "reason_code": "ACCEPTED",
                "selected_stage": "section",
                "stage_artifacts": [stage_artifact],
                "top_candidates": [_top_candidate(paragraph_id="fls_wrong999")],
            },
        },
    )

    report = validate_fls_ws7.run_validation()

    assert report["rows"][0]["investigation_record"]["failure_layer"] == "stage_scope_failure"
    assert report["rows"][0]["triage_classification"] == "weak_mapping"
    assert report["rows"][0]["runtime_queue"] is False


def test_run_validation_rejects_count_mismatch(monkeypatch) -> None:
    artifact = _stage_artifact()
    artifact["mode_artifacts"]["lexical"]["qualifying_candidate_count"] = 2
    monkeypatch.setattr(
        validate_fls_ws7,
        "load_calibration_items",
        lambda **kwargs: [
            {
                "path": "x.rst",
                "packet": {"construct_terms": ["atomic"]},
                "acceptable_ids": ["fls_atomic002"],
            }
        ],
    )
    monkeypatch.setattr(
        validate_fls_ws7,
        "resolve_fls_for_guideline",
        lambda packet: {
            "paragraph_id": "fls_atomic002",
            "decision": {
                "accepted": True,
                "review_candidate": False,
                "reason_code": "ACCEPTED",
                "selected_stage": "global",
                "stage_artifacts": [artifact],
                "top_candidates": [_top_candidate()],
            },
        },
    )

    report = validate_fls_ws7.run_validation()

    assert report["structural_failures"] == 1
    assert report["proof_valid"] is False


def test_run_validation_rejects_missing_score_component(monkeypatch) -> None:
    candidate = _top_candidate()
    del candidate["score_components"]["ambiguity_penalty"]
    monkeypatch.setattr(
        validate_fls_ws7,
        "load_calibration_items",
        lambda **kwargs: [
            {
                "path": "x.rst",
                "packet": {"construct_terms": ["atomic"]},
                "acceptable_ids": ["fls_atomic002"],
            }
        ],
    )
    monkeypatch.setattr(
        validate_fls_ws7,
        "resolve_fls_for_guideline",
        lambda packet: {
            "paragraph_id": "fls_atomic002",
            "decision": {
                "accepted": True,
                "review_candidate": False,
                "reason_code": "ACCEPTED",
                "selected_stage": "global",
                "stage_artifacts": [_stage_artifact()],
                "top_candidates": [candidate],
            },
        },
    )

    report = validate_fls_ws7.run_validation()

    assert report["structural_failures"] == 1
    assert report["proof_valid"] is False


def test_run_validation_rejects_top_candidate_missing_from_stage(monkeypatch) -> None:
    monkeypatch.setattr(
        validate_fls_ws7,
        "load_calibration_items",
        lambda **kwargs: [
            {
                "path": "x.rst",
                "packet": {"construct_terms": ["atomic"]},
                "acceptable_ids": ["fls_atomic002"],
            }
        ],
    )
    monkeypatch.setattr(
        validate_fls_ws7,
        "resolve_fls_for_guideline",
        lambda packet: {
            "paragraph_id": "fls_atomic002",
            "decision": {
                "accepted": True,
                "review_candidate": False,
                "reason_code": "ACCEPTED",
                "selected_stage": "global",
                "selected_candidate": {"paragraph_id": "fls_atomic002"},
                "stage_artifacts": [_stage_artifact(paragraph_id="fls_other")],
                "top_candidates": [_top_candidate()],
            },
        },
    )

    report = validate_fls_ws7.run_validation()

    assert report["structural_failures"] == 1
    assert report["proof_valid"] is False
