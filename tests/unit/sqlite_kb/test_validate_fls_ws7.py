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
            "document_prior_score": 0.0,
            "section_prior_score": 0.0,
            "defined_term_match_score": 0.0,
            "term_ref_match_score": 0.0,
            "syntax_match_score": 0.0,
            "std_ref_match_score": 0.0,
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
