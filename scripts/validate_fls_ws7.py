"""Produce WS7 staged retrieval validation artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import yaml

try:
    from context.exemplars import EXEMPLAR_MANIFEST
    from context.fls_lookup import resolve_fls_for_guideline

    from retrieval.writer_host.fls_calibration import load_calibration_items
except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(PROJECT_ROOT))
    from context.exemplars import EXEMPLAR_MANIFEST
    from context.fls_lookup import resolve_fls_for_guideline

    from retrieval.writer_host.fls_calibration import load_calibration_items


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUIDELINES_REPO = Path(
    os.environ.get(
        "GUIDELINES_REPO", "/Users/pete.levasseur/personal/safety-critical-rust-coding-guidelines"
    )
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT / ".cache" / "sqlite_kb" / "reports" / "fls_spec" / "ws7_validation.json"
)
EXPECTED_STAGE_ARTIFACT_KEYS = {
    "stage_name",
    "mode_artifacts",
    "candidate_universe_size",
    "advancement_reason",
    "candidate_ids",
}
EXPECTED_STAGE_CANDIDATE_KEYS = {
    "chunk_uid",
    "paragraph_id",
    "paragraph_link",
    "first_seen_stage",
    "seen_in_modes",
    "mode_row_refs",
}
EXPECTED_MODE_ARTIFACT_KEYS = {
    "requested_mode",
    "executed_mode",
    "returned_candidate_count",
    "qualifying_candidate_count",
    "retrieval_result_ref",
}
ALLOWED_ADVANCEMENT_REASONS = {
    "NO_QUALIFYING_CANDIDATES",
    "TERMINAL_STAGE_SUCCESS",
    "GLOBAL_FALLBACK_REQUIRED",
    "VALIDATION_ONLY_CONTINUATION",
}
ALLOWED_MODES = {"lexical", "semantic", "hybrid"}
EXPECTED_RETRIEVAL_RESULT_REF_KEYS = {
    "stage",
    "query_text",
    "scope",
    "qualifying_paragraph_ids",
    "rows",
}
EXPECTED_RETRIEVAL_ROW_KEYS = {"chunk_uid", "paragraph_id", "paragraph_link"}
EXPECTED_MODE_ROW_REF_KEYS = {"rank", "stage", "chunk_uid", "paragraph_id"}
FAILURE_LAYERS = {
    "grounding_prior_failure",
    "stage_scope_failure",
    "retrieval_recall_failure",
    "candidate_merge_failure",
    "qualification_failure",
    "candidate_scoring_failure",
    "stage_termination_failure",
    "glossary_decision_failure",
    "mapping_quality_failure",
    "corpus_gap",
    "none",
}
TRIAGE_CLASSIFICATIONS = {
    "true_ranking_bug",
    "stale_mapping",
    "weak_mapping",
    "corpus_gap",
    "expected_abstention",
}


def _load_expected_score_components() -> set[str]:
    payload = (
        yaml.safe_load(
            (PROJECT_ROOT / "config" / "fls_resolution_policy.yaml").read_text(encoding="utf-8")
        )
        or {}
    )
    components = ((payload.get("ws7_policy") or {}).get("scoring") or {}).get("components") or {}
    return {str(name).strip() for name in components if str(name).strip()}


def _dataset_metadata(dataset_path: Path | None) -> dict[str, Any]:
    if dataset_path is None:
        return {
            "source": "<exemplar_manifest>",
            "fingerprint_sha256": "",
            "frozen": False,
            "runtime_use_prohibited": False,
        }
    raw = dataset_path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        payload = {}
    return {
        "source": str(dataset_path),
        "fingerprint_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "frozen": bool(payload.get("frozen", False)),
        "runtime_use_prohibited": bool(payload.get("runtime_use_prohibited", False)),
        "manifest_version": payload.get("manifest_version"),
        "purpose": str(payload.get("purpose", "")).strip(),
    }


def _candidate_trace(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_uid": str(row.get("chunk_uid", "")),
        "paragraph_id": str(row.get("paragraph_id", "")),
        "paragraph_link": str(row.get("paragraph_link", "")),
        "retrieval_stage": str(row.get("retrieval_stage", "")),
        "first_seen_stage": str(row.get("first_seen_stage", "")),
        "seen_in_modes": list(row.get("seen_in_modes") or []),
        "mode_row_refs": dict(row.get("mode_row_refs") or {}),
        "score_components": dict(row.get("score_components") or {}),
        "total_score": float(row.get("total_score", 0.0) or 0.0),
        "glossary_candidate": bool(row.get("glossary_candidate", False)),
        "matched_role_features": dict(row.get("matched_role_features") or {}),
        "canonical_merge": dict(row.get("canonical_merge") or {}),
        "qualifying_candidate": bool(row.get("qualifying_candidate", False)),
    }


def _grounding_snapshot(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "governing_obligation": str(packet.get("governing_obligation", "")),
        "construct_terms": list(packet.get("construct_terms") or []),
        "supporting_phrases": list(packet.get("supporting_phrases") or []),
        "code_tokens": list(packet.get("code_tokens") or []),
        "prior_documents": list(packet.get("prior_documents") or []),
        "prior_sections": list(packet.get("prior_sections") or []),
        "ambiguity_notes": list(packet.get("ambiguity_notes") or []),
    }


def _stage_artifact_by_name(stage_artifacts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(stage.get("stage_name", "")): stage
        for stage in stage_artifacts
        if isinstance(stage, dict) and str(stage.get("stage_name", "")).strip()
    }


def _candidate_presence(
    stage_artifacts: list[dict[str, Any]], acceptable_ids: list[str]
) -> dict[str, Any]:
    acceptable = {str(value).strip() for value in acceptable_ids if str(value).strip()}
    by_name = _stage_artifact_by_name(stage_artifacts)
    result: dict[str, Any] = {}
    for stage_name in ("section", "document", "global"):
        candidates = list((by_name.get(stage_name) or {}).get("candidate_ids") or [])
        ids = {
            str(candidate.get("paragraph_id", "")).strip()
            for candidate in candidates
            if isinstance(candidate, dict)
        }
        result[f"expected_candidate_entered_{stage_name}"] = bool(ids & acceptable)
    return result


def _top_candidate_component_diff(
    top_candidates: list[dict[str, Any]], acceptable_ids: list[str]
) -> dict[str, Any]:
    acceptable = {str(value).strip() for value in acceptable_ids if str(value).strip()}
    observed = top_candidates[0] if top_candidates else {}
    expected = next(
        (row for row in top_candidates if str(row.get("paragraph_id", "")).strip() in acceptable),
        {},
    )
    expected_components = dict(expected.get("score_components") or {})
    observed_components = dict(observed.get("score_components") or {})
    diff_rows: list[dict[str, Any]] = []
    for key in sorted(set(expected_components) | set(observed_components)):
        expected_value = float(expected_components.get(key, 0.0) or 0.0)
        observed_value = float(observed_components.get(key, 0.0) or 0.0)
        diff_rows.append(
            {
                "component": key,
                "expected": expected_value,
                "observed": observed_value,
                "delta": round(expected_value - observed_value, 6),
            }
        )
    diff_rows.sort(key=lambda row: (-abs(float(row["delta"])), row["component"]))
    return {
        "expected_paragraph_id": str(expected.get("paragraph_id", "")),
        "observed_paragraph_id": str(observed.get("paragraph_id", "")),
        "rows": diff_rows[:8],
    }


def _score_component_losses(
    top_candidates: list[dict[str, Any]], acceptable_ids: list[str]
) -> list[str]:
    diff = _top_candidate_component_diff(top_candidates, acceptable_ids)
    losses = []
    for row in list(diff.get("rows") or []):
        if float(row.get("delta", 0.0) or 0.0) > 0.0:
            losses.append(str(row.get("component", "")))
    return losses


def _failure_layer(
    *,
    outcome: str,
    reason_code: str,
    selected_stage: str,
    acceptable_ids: list[str],
    top_candidates: list[dict[str, Any]],
    stage_artifacts: list[dict[str, Any]],
    packet: dict[str, Any],
) -> tuple[str, str, str]:
    presence = _candidate_presence(stage_artifacts, acceptable_ids)
    expected_in_any_stage = any(bool(value) for value in presence.values())
    expected_in_top = any(
        str(row.get("paragraph_id", "")).strip() in {str(value).strip() for value in acceptable_ids}
        for row in top_candidates
    )
    if outcome in {"accepted-correct", "review-correct", "unresolved-expected"}:
        if outcome == "review-correct":
            return (
                "candidate_scoring_failure",
                "expected_candidate_competed_but_remains_review_only",
                "context/fls_ws7.py",
            )
        return ("none", "no_open_failure", "")
    if not list(packet.get("prior_documents") or []) and not list(
        packet.get("prior_sections") or []
    ):
        return (
            "grounding_prior_failure",
            "empty_prior_surface",
            "scripts/retrieval/writer_host/fls_grounding.py",
        )
    if not presence.get("expected_candidate_entered_section", False) and not presence.get(
        "expected_candidate_entered_document", False
    ):
        return (
            "stage_scope_failure",
            "expected_candidate_missing_from_scoped_stages",
            "context/fls_ws7.py",
        )
    if not expected_in_any_stage:
        return (
            "retrieval_recall_failure",
            "expected_candidate_never_entered_candidate_ids",
            "context/fls_ws7.py",
        )
    if expected_in_any_stage and not expected_in_top:
        return (
            "qualification_failure",
            "expected_candidate_entered_but_failed_qualification_or_top_candidate_cut",
            "context/fls_ws7.py",
        )
    if "GLOSSARY" in reason_code.upper() or any(
        bool(row.get("glossary_candidate", False)) for row in top_candidates[:1]
    ):
        return (
            "glossary_decision_failure",
            "glossary_decision_controls_outcome",
            "context/fls_ws7.py",
        )
    if selected_stage in {"section", "document"} and reason_code.startswith(
        "SCOPED_STAGE_NON_TERMINAL"
    ):
        return ("stage_termination_failure", reason_code.lower(), "context/fls_ws7.py")
    return (
        "candidate_scoring_failure",
        "score_component_competitiveness_gap",
        "context/fls_ws7.py",
    )


def _investigation_record(
    *,
    path: str,
    acceptable_ids: list[str],
    resolved_paragraph_id: str,
    outcome: str,
    reason_code: str,
    selected_stage: str,
    top_candidates: list[dict[str, Any]],
    stage_artifacts: list[dict[str, Any]],
    packet: dict[str, Any],
) -> dict[str, Any]:
    failure_layer, failure_subtype, next_surface = _failure_layer(
        outcome=outcome,
        reason_code=reason_code,
        selected_stage=selected_stage,
        acceptable_ids=acceptable_ids,
        top_candidates=top_candidates,
        stage_artifacts=stage_artifacts,
        packet=packet,
    )
    presence = _candidate_presence(stage_artifacts, acceptable_ids)
    score_losses = _score_component_losses(top_candidates, acceptable_ids)
    acceptable = {str(value).strip() for value in acceptable_ids if str(value).strip()}
    expected_top = next(
        (row for row in top_candidates if str(row.get("paragraph_id", "")).strip() in acceptable),
        {},
    )
    evidence_summary = {
        "prior_surface_ok": bool(
            list(packet.get("prior_documents") or []) or list(packet.get("prior_sections") or [])
        ),
        **presence,
        "expected_candidate_top5": bool(expected_top),
        "expected_candidate_lost_on_components": score_losses,
    }
    return {
        "item_id": Path(path).stem,
        "expected_ids": list(acceptable_ids),
        "observed_id": resolved_paragraph_id,
        "observed_outcome": outcome,
        "failure_layer": failure_layer,
        "failure_subtype": failure_subtype,
        "evidence_summary": evidence_summary,
        "next_change_surface": next_surface,
    }


def _proof_bundle(
    *,
    packet: dict[str, Any],
    acceptable_ids: list[str],
    stage_artifacts: list[dict[str, Any]],
    top_candidates: list[dict[str, Any]],
    trace_path: str,
) -> dict[str, Any]:
    return {
        "routing_artifact": {
            "kind": "scoped_candidate_universe_diff",
            "trace_path": trace_path,
            "stage_sequence": [
                str(stage.get("stage_name", ""))
                for stage in stage_artifacts
                if isinstance(stage, dict)
            ],
            "candidate_presence": _candidate_presence(stage_artifacts, acceptable_ids),
        },
        "ranking_artifact": {
            "kind": "score_component_diff",
            "comparison": _top_candidate_component_diff(top_candidates, acceptable_ids),
        },
        "structural_artifact": {
            "kind": "validation_report_row",
            "grounding_snapshot": _grounding_snapshot(packet),
        },
    }


def _triage_classification(
    *,
    outcome: str,
    investigation_record: dict[str, Any],
    packet: dict[str, Any],
) -> dict[str, Any]:
    evidence = dict(investigation_record.get("evidence_summary") or {})
    expected_entered_any = any(
        bool(evidence.get(key, False))
        for key in (
            "expected_candidate_entered_section",
            "expected_candidate_entered_document",
            "expected_candidate_entered_global",
            "expected_candidate_top5",
        )
    )
    if outcome in {"accepted-correct", "review-correct", "unresolved-expected"}:
        return {"classification": "expected_abstention", "runtime_queue": False}
    if expected_entered_any:
        return {"classification": "true_ranking_bug", "runtime_queue": True}
    if list(packet.get("prior_documents") or []) or list(packet.get("prior_sections") or []):
        return {"classification": "weak_mapping", "runtime_queue": False}
    return {"classification": "corpus_gap", "runtime_queue": False}


def _validate_stage_artifacts(stage_artifacts: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    for index, artifact in enumerate(stage_artifacts):
        prefix = f"stage_artifacts[{index}]"
        keys = set(artifact)
        if keys != EXPECTED_STAGE_ARTIFACT_KEYS:
            problems.append(
                f"{prefix} keys {sorted(keys)} != {sorted(EXPECTED_STAGE_ARTIFACT_KEYS)}"
            )
        if str(artifact.get("advancement_reason", "")) not in ALLOWED_ADVANCEMENT_REASONS:
            problems.append(f"{prefix} has invalid advancement_reason")
        mode_artifacts = artifact.get("mode_artifacts")
        if not isinstance(mode_artifacts, dict) or set(mode_artifacts) != {
            "lexical",
            "semantic",
            "hybrid",
        }:
            problems.append(f"{prefix} must include lexical/semantic/hybrid mode_artifacts")
            continue
        for mode, mode_artifact in mode_artifacts.items():
            mode_prefix = f"{prefix}.mode_artifacts[{mode}]"
            if mode not in ALLOWED_MODES:
                problems.append(f"{mode_prefix} uses undeclared mode")
            if set(mode_artifact) != EXPECTED_MODE_ARTIFACT_KEYS:
                problems.append(
                    f"{mode_prefix} keys {sorted(mode_artifact)} != "
                    f"{sorted(EXPECTED_MODE_ARTIFACT_KEYS)}"
                )
            retrieval_result_ref = mode_artifact.get("retrieval_result_ref")
            if not isinstance(retrieval_result_ref, dict):
                problems.append(f"{mode_prefix}.retrieval_result_ref must be a dict")
                continue
            if set(retrieval_result_ref) != EXPECTED_RETRIEVAL_RESULT_REF_KEYS:
                problems.append(
                    f"{mode_prefix}.retrieval_result_ref keys {sorted(retrieval_result_ref)} != "
                    f"{sorted(EXPECTED_RETRIEVAL_RESULT_REF_KEYS)}"
                )
            scope = retrieval_result_ref.get("scope")
            if not isinstance(scope, dict) or not str(scope.get("state", "")).strip():
                problems.append(f"{mode_prefix}.retrieval_result_ref.scope must include state")
            rows = retrieval_result_ref.get("rows")
            if not isinstance(rows, list):
                problems.append(f"{mode_prefix}.retrieval_result_ref.rows must be a list")
            else:
                if len(rows) != int(mode_artifact.get("returned_candidate_count", -1)):
                    problems.append(
                        f"{mode_prefix} returned_candidate_count does not match rows length"
                    )
                for row_index, row in enumerate(rows):
                    row_prefix = f"{mode_prefix}.retrieval_result_ref.rows[{row_index}]"
                    if set(row) != EXPECTED_RETRIEVAL_ROW_KEYS:
                        problems.append(
                            f"{row_prefix} keys {sorted(row)} != "
                            f"{sorted(EXPECTED_RETRIEVAL_ROW_KEYS)}"
                        )
            qualifying_ids = retrieval_result_ref.get("qualifying_paragraph_ids")
            if not isinstance(qualifying_ids, list):
                problems.append(
                    f"{mode_prefix}.retrieval_result_ref.qualifying_paragraph_ids must be a list"
                )
            else:
                normalized_ids = [
                    str(value).strip() for value in qualifying_ids if str(value).strip()
                ]
                if len(normalized_ids) != len(qualifying_ids):
                    problems.append(f"{mode_prefix}.qualifying_paragraph_ids contains blank values")
                if len(normalized_ids) != int(mode_artifact.get("qualifying_candidate_count", -1)):
                    problems.append(
                        f"{mode_prefix} qualifying_candidate_count does not match "
                        "qualifying ids length"
                    )
                if isinstance(rows, list):
                    row_ids = {
                        str(row.get("paragraph_id", "")).strip()
                        for row in rows
                        if str(row.get("paragraph_id", "")).strip()
                    }
                    if not set(normalized_ids).issubset(row_ids):
                        problems.append(
                            f"{mode_prefix}.qualifying_paragraph_ids must be subset "
                            "of retrieval rows"
                        )
        candidate_ids = artifact.get("candidate_ids")
        if not isinstance(candidate_ids, list):
            problems.append(f"{prefix}.candidate_ids must be a list")
            continue
        candidate_paragraph_ids: set[str] = set()
        for candidate_index, candidate in enumerate(candidate_ids):
            candidate_prefix = f"{prefix}.candidate_ids[{candidate_index}]"
            if set(candidate) != EXPECTED_STAGE_CANDIDATE_KEYS:
                problems.append(
                    f"{candidate_prefix} keys {sorted(candidate)} != "
                    f"{sorted(EXPECTED_STAGE_CANDIDATE_KEYS)}"
                )
            mode_row_refs = candidate.get("mode_row_refs")
            if not isinstance(mode_row_refs, dict):
                problems.append(f"{candidate_prefix}.mode_row_refs must be a dict")
                continue
            candidate_paragraph_id = str(candidate.get("paragraph_id", "")).strip()
            candidate_paragraph_ids.add(candidate_paragraph_id)
            seen_in_modes = list(candidate.get("seen_in_modes") or [])
            if set(seen_in_modes) != set(mode_row_refs):
                problems.append(
                    f"{candidate_prefix}.seen_in_modes must match mode_row_refs keys exactly"
                )
            for mode, ref in mode_row_refs.items():
                ref_prefix = f"{candidate_prefix}.mode_row_refs[{mode}]"
                if mode not in ALLOWED_MODES:
                    problems.append(f"{ref_prefix} uses undeclared mode")
                if not isinstance(ref, dict) or set(ref) != EXPECTED_MODE_ROW_REF_KEYS:
                    problems.append(
                        f"{ref_prefix} must be a dict with keys "
                        f"{sorted(EXPECTED_MODE_ROW_REF_KEYS)}"
                    )
                    continue
                if str(ref.get("paragraph_id", "")).strip() != candidate_paragraph_id:
                    problems.append(
                        f"{ref_prefix}.paragraph_id must match parent candidate paragraph_id"
                    )
                mode_artifact = mode_artifacts.get(mode)
                retrieval_rows = ((mode_artifact or {}).get("retrieval_result_ref") or {}).get(
                    "rows"
                ) or []
                retrieval_keys = {
                    (
                        str(row.get("chunk_uid", "")).strip(),
                        str(row.get("paragraph_id", "")).strip(),
                        str(row.get("paragraph_link", "")).strip(),
                    )
                    for row in retrieval_rows
                }
                ref_key = (
                    str(ref.get("chunk_uid", "")).strip(),
                    str(ref.get("paragraph_id", "")).strip(),
                    str(candidate.get("paragraph_link", "")).strip(),
                )
                if ref_key not in retrieval_keys:
                    problems.append(f"{ref_prefix} must refer to a retrieval row in mode_artifacts")
        for mode, mode_artifact in mode_artifacts.items():
            qualifying_ids = (
                (
                    (mode_artifact.get("retrieval_result_ref") or {}).get(
                        "qualifying_paragraph_ids"
                    )
                    or []
                )
                if isinstance(mode_artifact, dict)
                else []
            )
            if not set(
                str(value).strip() for value in qualifying_ids if str(value).strip()
            ).issubset(candidate_paragraph_ids):
                problems.append(
                    f"{prefix}.mode_artifacts[{mode}] qualifying ids missing from candidate_ids"
                )
    return problems


def _classify_outcome(
    *,
    resolved_paragraph_id: str,
    acceptable_ids: list[str],
    accepted: bool,
    review_candidate: bool,
    should_abstain: bool,
    allow_review: bool,
    allow_unresolved: bool,
) -> str:
    if should_abstain:
        if resolved_paragraph_id == "fls_UNRESOLVED":
            return "unresolved-expected"
        if review_candidate:
            return "review-correct"
        return "accepted-wrong"
    correct = resolved_paragraph_id in set(acceptable_ids)
    if accepted:
        return "accepted-correct" if correct else "accepted-wrong"
    if review_candidate:
        return "review-correct" if correct and allow_review else "review-unexpected"
    return "unresolved-expected" if allow_unresolved else "unresolved-unexpected"


def _validate_top_candidates(top_candidates: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    expected_components = _load_expected_score_components()
    for index, row in enumerate(top_candidates):
        prefix = f"top_candidates[{index}]"
        mode_row_refs = row.get("mode_row_refs")
        if not isinstance(mode_row_refs, dict):
            problems.append(f"{prefix}.mode_row_refs must be a dict")
        score_components = row.get("score_components")
        if set(score_components or {}) != expected_components:
            problems.append(
                f"{prefix}.score_components keys {sorted(score_components or {})} != "
                f"{sorted(expected_components)}"
            )
        if not str(row.get("paragraph_id", "")).strip():
            problems.append(f"{prefix}.paragraph_id must be present")
        if not str(row.get("paragraph_link", "")).strip():
            problems.append(f"{prefix}.paragraph_link must be present")
        if not str(row.get("first_seen_stage", "")).strip():
            problems.append(f"{prefix}.first_seen_stage must be present")
        canonical_merge = row.get("canonical_merge")
        if not isinstance(canonical_merge, dict):
            problems.append(f"{prefix}.canonical_merge must be a dict")
        else:
            identity_conflicts = canonical_merge.get("identity_conflicts")
            selected_values = canonical_merge.get("selected_values")
            if not isinstance(identity_conflicts, dict):
                problems.append(f"{prefix}.canonical_merge.identity_conflicts must be a dict")
            if not isinstance(selected_values, dict):
                problems.append(f"{prefix}.canonical_merge.selected_values must be a dict")
    return problems


def _validate_row_against_stage_artifacts(
    *,
    top_candidates: list[dict[str, Any]],
    stage_artifacts: list[dict[str, Any]],
    selected_stage: str,
    selected_candidate: dict[str, Any],
) -> list[str]:
    problems: list[str] = []
    stage_by_name = {
        str(stage.get("stage_name", "")): stage
        for stage in stage_artifacts
        if isinstance(stage, dict)
    }
    selected_stage_artifact = stage_by_name.get(selected_stage)
    if selected_stage and selected_stage_artifact is None:
        problems.append("selected_stage is missing from stage_artifacts")
        return problems
    candidate_ids = {
        str(candidate.get("paragraph_id", "")): candidate
        for candidate in list((selected_stage_artifact or {}).get("candidate_ids") or [])
        if isinstance(candidate, dict)
    }
    for index, row in enumerate(top_candidates):
        prefix = f"top_candidates[{index}]"
        paragraph_id = str(row.get("paragraph_id", "")).strip()
        stage_candidate = candidate_ids.get(paragraph_id)
        if selected_stage_artifact is not None and stage_candidate is None:
            problems.append(f"{prefix}.paragraph_id missing from selected-stage candidate_ids")
            continue
        if stage_candidate is not None:
            if (
                str(stage_candidate.get("paragraph_link", "")).strip()
                != str(row.get("paragraph_link", "")).strip()
            ):
                problems.append(f"{prefix}.paragraph_link mismatch vs selected-stage candidate_ids")
            if set(list(stage_candidate.get("seen_in_modes") or [])) != set(
                list(row.get("seen_in_modes") or [])
            ):
                problems.append(f"{prefix}.seen_in_modes mismatch vs selected-stage candidate_ids")
    selected_paragraph_id = str(selected_candidate.get("paragraph_id", "")).strip()
    if selected_paragraph_id:
        if selected_paragraph_id not in candidate_ids:
            problems.append("selected_candidate missing from selected-stage candidate_ids")
        top_candidate_ids = {
            str(candidate.get("paragraph_id", "")).strip()
            for candidate in top_candidates
            if isinstance(candidate, dict)
        }
        if selected_paragraph_id not in top_candidate_ids:
            problems.append("selected_candidate missing from top_candidates")
    return problems


def run_validation(*, dataset_path: Path | None = None) -> dict[str, Any]:
    dataset_meta = _dataset_metadata(dataset_path)
    items = load_calibration_items(
        manifest_path=EXEMPLAR_MANIFEST,
        guidelines_repo_root=GUIDELINES_REPO,
        dataset_path=dataset_path,
    )
    rows: list[dict[str, Any]] = []
    counters = {
        "accepted_correct": 0,
        "accepted_wrong": 0,
        "review_correct": 0,
        "review_unexpected": 0,
        "unresolved_expected": 0,
        "unresolved_unexpected": 0,
    }
    structural_failures = 0
    resolved = 0
    accepted = 0
    debug_records: list[dict[str, Any]] = []
    for item in items:
        packet = dict(item.get("packet") or {})
        predicted = resolve_fls_for_guideline(packet)
        decision = dict(predicted.get("decision") or {})
        stage_artifacts = list(decision.get("stage_artifacts") or [])
        structural_problems = _validate_stage_artifacts(stage_artifacts)
        top_candidates = [
            _candidate_trace(row) for row in list(decision.get("top_candidates") or [])
        ]
        structural_problems.extend(_validate_top_candidates(top_candidates))
        structural_problems.extend(
            _validate_row_against_stage_artifacts(
                top_candidates=top_candidates,
                stage_artifacts=stage_artifacts,
                selected_stage=str(decision.get("selected_stage", "")),
                selected_candidate=dict(decision.get("selected_candidate") or {}),
            )
        )
        resolved_paragraph_id = str(predicted.get("paragraph_id", ""))
        if resolved_paragraph_id and resolved_paragraph_id != "fls_UNRESOLVED":
            resolved += 1
        if bool(decision.get("accepted", False)):
            accepted += 1
        outcome = _classify_outcome(
            resolved_paragraph_id=resolved_paragraph_id,
            acceptable_ids=list(item.get("acceptable_ids") or []),
            accepted=bool(decision.get("accepted", False)),
            review_candidate=bool(decision.get("review_candidate", False)),
            should_abstain=bool(item.get("should_abstain", False)),
            allow_review=bool(item.get("allow_review", False)),
            allow_unresolved=bool(item.get("allow_unresolved", False)),
        )
        counters[outcome.replace("-", "_")] += 1
        if structural_problems:
            structural_failures += 1
        investigation_record = _investigation_record(
            path=str(item.get("path", "")),
            acceptable_ids=list(item.get("acceptable_ids") or []),
            resolved_paragraph_id=resolved_paragraph_id,
            outcome=outcome,
            reason_code=str(decision.get("reason_code", "")),
            selected_stage=str(decision.get("selected_stage", "")),
            top_candidates=top_candidates,
            stage_artifacts=stage_artifacts,
            packet=packet,
        )
        proof_bundle = _proof_bundle(
            packet=packet,
            acceptable_ids=list(item.get("acceptable_ids") or []),
            stage_artifacts=stage_artifacts,
            top_candidates=top_candidates,
            trace_path="",
        )
        triage = _triage_classification(
            outcome=outcome,
            investigation_record=investigation_record,
            packet=packet,
        )
        debug_records.append(investigation_record)
        rows.append(
            {
                "path": str(item.get("path", "")),
                "rationale": str(item.get("rationale", "")).strip(),
                "provenance": dict(item.get("provenance") or {}),
                "acceptable_ids": list(item.get("acceptable_ids") or []),
                "resolved_paragraph_id": resolved_paragraph_id,
                "selected_stage": str(decision.get("selected_stage", "")),
                "reason_code": str(decision.get("reason_code", "")),
                "accepted": bool(decision.get("accepted", False)),
                "review_candidate": bool(decision.get("review_candidate", False)),
                "outcome": outcome,
                "structural_problems": structural_problems,
                "stage_sequence_entered": [
                    str(stage.get("stage_name", ""))
                    for stage in stage_artifacts
                    if isinstance(stage, dict)
                ],
                "stage_artifacts": stage_artifacts,
                "top_candidates": top_candidates,
                "grounding_artifact_snapshot": _grounding_snapshot(packet),
                "investigation_record": investigation_record,
                "proof_bundle": proof_bundle,
                "triage_classification": str(triage["classification"]),
                "runtime_queue": bool(triage["runtime_queue"]),
            }
        )
    return {
        "runtime_mode": "ws7_staged_retrieval_v1",
        "dataset": dataset_meta,
        "item_count": len(items),
        "resolved_count": resolved,
        "accepted_count": accepted,
        "accepted_correct": counters["accepted_correct"],
        "accepted_wrong": counters["accepted_wrong"],
        "review_correct": counters["review_correct"],
        "review_unexpected": counters["review_unexpected"],
        "unresolved_expected": counters["unresolved_expected"],
        "unresolved_unexpected": counters["unresolved_unexpected"],
        "structural_failures": structural_failures,
        "investigation_records": debug_records,
        "triage_counts": {
            label: sum(1 for row in rows if str(row.get("triage_classification", "")) == label)
            for label in sorted(TRIAGE_CLASSIFICATIONS)
        },
        "proof_valid": structural_failures == 0
        and counters["accepted_wrong"] == 0
        and counters["review_unexpected"] == 0
        and counters["unresolved_unexpected"] == 0,
        "rows": rows,
    }


def run_validation_with_progress(
    *,
    dataset_path: Path | None = None,
    trace_dir: Path | None = None,
    validation_only_continuation: bool = False,
) -> dict[str, Any]:
    dataset_meta = _dataset_metadata(dataset_path)
    items = load_calibration_items(
        manifest_path=EXEMPLAR_MANIFEST,
        guidelines_repo_root=GUIDELINES_REPO,
        dataset_path=dataset_path,
    )
    rows: list[dict[str, Any]] = []
    counters = {
        "accepted_correct": 0,
        "accepted_wrong": 0,
        "review_correct": 0,
        "review_unexpected": 0,
        "unresolved_expected": 0,
        "unresolved_unexpected": 0,
    }
    structural_failures = 0
    resolved = 0
    accepted = 0
    debug_records: list[dict[str, Any]] = []
    total = len(items)
    for index, item in enumerate(items, start=1):
        started = time.time()
        path = str(item.get("path", ""))
        print(f"[{index}/{total}] start {path}", flush=True)
        trace_path = None
        if trace_dir is not None:
            trace_path = trace_dir / f"{index:03d}_{Path(path).stem}.jsonl"
        packet = dict(item.get("packet") or {})
        predicted = resolve_fls_for_guideline(
            packet,
            policy_overrides={
                "validation_only_continuation": validation_only_continuation,
                "trace_path": str(trace_path) if trace_path is not None else "",
            },
        )
        decision = dict(predicted.get("decision") or {})
        stage_artifacts = list(decision.get("stage_artifacts") or [])
        structural_problems = _validate_stage_artifacts(stage_artifacts)
        top_candidates = [
            _candidate_trace(row) for row in list(decision.get("top_candidates") or [])
        ]
        structural_problems.extend(_validate_top_candidates(top_candidates))
        structural_problems.extend(
            _validate_row_against_stage_artifacts(
                top_candidates=top_candidates,
                stage_artifacts=stage_artifacts,
                selected_stage=str(decision.get("selected_stage", "")),
                selected_candidate=dict(decision.get("selected_candidate") or {}),
            )
        )
        resolved_paragraph_id = str(predicted.get("paragraph_id", ""))
        if resolved_paragraph_id and resolved_paragraph_id != "fls_UNRESOLVED":
            resolved += 1
        if bool(decision.get("accepted", False)):
            accepted += 1
        outcome = _classify_outcome(
            resolved_paragraph_id=resolved_paragraph_id,
            acceptable_ids=list(item.get("acceptable_ids") or []),
            accepted=bool(decision.get("accepted", False)),
            review_candidate=bool(decision.get("review_candidate", False)),
            should_abstain=bool(item.get("should_abstain", False)),
            allow_review=bool(item.get("allow_review", False)),
            allow_unresolved=bool(item.get("allow_unresolved", False)),
        )
        counters[outcome.replace("-", "_")] += 1
        if structural_problems:
            structural_failures += 1
        investigation_record = _investigation_record(
            path=path,
            acceptable_ids=list(item.get("acceptable_ids") or []),
            resolved_paragraph_id=resolved_paragraph_id,
            outcome=outcome,
            reason_code=str(decision.get("reason_code", "")),
            selected_stage=str(decision.get("selected_stage", "")),
            top_candidates=top_candidates,
            stage_artifacts=stage_artifacts,
            packet=packet,
        )
        proof_bundle = _proof_bundle(
            packet=packet,
            acceptable_ids=list(item.get("acceptable_ids") or []),
            stage_artifacts=stage_artifacts,
            top_candidates=top_candidates,
            trace_path=str(trace_path) if trace_path is not None else "",
        )
        triage = _triage_classification(
            outcome=outcome,
            investigation_record=investigation_record,
            packet=packet,
        )
        row = {
            "path": path,
            "rationale": str(item.get("rationale", "")).strip(),
            "provenance": dict(item.get("provenance") or {}),
            "acceptable_ids": list(item.get("acceptable_ids") or []),
            "resolved_paragraph_id": resolved_paragraph_id,
            "selected_stage": str(decision.get("selected_stage", "")),
            "reason_code": str(decision.get("reason_code", "")),
            "accepted": bool(decision.get("accepted", False)),
            "review_candidate": bool(decision.get("review_candidate", False)),
            "outcome": outcome,
            "structural_problems": structural_problems,
            "stage_sequence_entered": [
                str(stage.get("stage_name", ""))
                for stage in stage_artifacts
                if isinstance(stage, dict)
            ],
            "stage_artifacts": stage_artifacts,
            "top_candidates": top_candidates,
            "trace_path": str(trace_path) if trace_path is not None else "",
            "grounding_artifact_snapshot": _grounding_snapshot(packet),
            "investigation_record": investigation_record,
            "proof_bundle": proof_bundle,
            "triage_classification": str(triage["classification"]),
            "runtime_queue": bool(triage["runtime_queue"]),
        }
        debug_records.append(investigation_record)
        rows.append(row)
        elapsed = time.time() - started
        print(
            f"[{index}/{total}] done {path} -> {resolved_paragraph_id} "
            f"{row['reason_code']} {outcome} in {elapsed:.2f}s",
            flush=True,
        )
    return {
        "runtime_mode": "ws7_staged_retrieval_v1",
        "dataset": dataset_meta,
        "item_count": len(items),
        "resolved_count": resolved,
        "accepted_count": accepted,
        "accepted_correct": counters["accepted_correct"],
        "accepted_wrong": counters["accepted_wrong"],
        "review_correct": counters["review_correct"],
        "review_unexpected": counters["review_unexpected"],
        "unresolved_expected": counters["unresolved_expected"],
        "unresolved_unexpected": counters["unresolved_unexpected"],
        "structural_failures": structural_failures,
        "investigation_records": debug_records,
        "triage_counts": {
            label: sum(1 for row in rows if str(row.get("triage_classification", "")) == label)
            for label in sorted(TRIAGE_CLASSIFICATIONS)
        },
        "proof_valid": structural_failures == 0
        and counters["accepted_wrong"] == 0
        and counters["review_unexpected"] == 0
        and counters["unresolved_unexpected"] == 0,
        "rows": rows,
    }


def write_validation_report(
    report: dict[str, Any], *, run_dir: Path | None = None, output_path: Path | None = None
) -> Path:
    out = output_path or (
        run_dir / "ws7_validation.json" if run_dir is not None else DEFAULT_OUTPUT
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate WS7 staged FLS retrieval and ranking")
    parser.add_argument("--dataset", default="", help="Optional calibration dataset JSON path")
    parser.add_argument("--run-dir", default="", help="Optional run directory")
    parser.add_argument("--output", default="", help="Optional explicit output path")
    parser.add_argument("--trace-dir", default="", help="Optional per-item JSONL trace directory")
    parser.add_argument(
        "--validation-only-continuation",
        action="store_true",
        help="Continue through all WS7 stages for tracing even after an early candidate is found",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dataset = Path(str(args.dataset).strip()).resolve() if str(args.dataset).strip() else None
    run_dir = Path(str(args.run_dir).strip()).resolve() if str(args.run_dir).strip() else None
    output_path = Path(str(args.output).strip()).resolve() if str(args.output).strip() else None
    trace_dir = Path(str(args.trace_dir).strip()).resolve() if str(args.trace_dir).strip() else None
    report = run_validation_with_progress(
        dataset_path=dataset,
        trace_dir=trace_dir,
        validation_only_continuation=bool(args.validation_only_continuation),
    )
    out = write_validation_report(report, run_dir=run_dir, output_path=output_path)
    print(f"WS7 items: {report['item_count']}")
    print(f"Resolved: {report['resolved_count']}")
    print(f"Accepted: {report['accepted_count']}")
    print(f"Accepted correct: {report['accepted_correct']}")
    print(f"Accepted wrong: {report['accepted_wrong']}")
    print(f"Structural failures: {report['structural_failures']}")
    print(f"Proof valid: {report['proof_valid']}")
    print(f"Report saved: {out}")
    return 0 if bool(report.get("proof_valid", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
