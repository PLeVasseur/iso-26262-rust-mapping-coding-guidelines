#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retrieval.core.policy_loader import load_eval_policy
from retrieval.core.profile_loader import (
    apply_profile_defaults,
    enforce_profile_corpus,
    load_retrieval_profile,
)
from retrieval.corpora.registry import get_corpus_adapter, list_supported_corpora
from retrieval.corpora.runtime_paths import resolve_corpus_runtime_paths
from retrieval.eval.prompt_routing import (
    HYBRID_FUSION_ROUTING_BEST_PRACTICE_V1,
    HYBRID_FUSION_ROUTING_OFF,
    classify_prompt_family,
    resolve_hybrid_fusion_method_for_case,
)
from retrieval.eval.metrics import (
    aggregate_duration_metrics,
    aggregate_mode_metrics,
    aggregate_projection_metrics,
    anchor_hit,
    mode_skew_alarm,
    mrr_at_k,
    ndcg_at_k,
    precision_at_k,
    projection_f1,
    row_hit,
)
from retrieval.eval.runtime_support import (
    is_relevant as _is_relevant,
    load_build_provenance as _load_build_provenance,
    load_trace_ids_by_context as _load_trace_ids_by_context,
    load_yaml_mapping as _load_yaml,
    utc_now as _utc_now,
    validate_required_evidence_fields as _validate_required_evidence_fields,
)
from retrieval.eval.reporting import (
    infer_root_cause_run_and_cell as eval_infer_root_cause_run_and_cell,
)
from retrieval.eval.reporting import write_eval_report
from retrieval.eval.runner import load_eval_prompts as eval_load_eval_prompts
from retrieval.operations.query import (
    ModeExecutionError,
    RowProjectionPolicy,
    execute_retrieval_query,
    resolve_row_projection_policy,
)
from semantic_backend_client import (
    SemanticBackendConfig,
    check_semantic_backend,
    resolve_embed_base_url,
    resolve_rerank_base_url,
)
from sqlite_query_guardrails import GuardrailError

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3
DEFAULT_TOP_K = 10
DEFAULT_CANDIDATE_LIMIT = 5000
SLICES = {"issue_identification", "resolution_identification"}
MODES = {"lexical", "semantic", "hybrid"}
SKEW_THRESHOLDS = {
    "max_row_share": 0.75,
    "min_row_entropy": 0.35,
    "min_abstain_rate_with_expected": 0.20,
}
THRESHOLDS = {
    "lexical": {
        "precision_at_k": 0.55,
        "mrr_at_k": 0.65,
        "row_hit_rate": 0.80,
    },
    "semantic_focus": {
        "mrr_at_k": 0.60,
        "row_hit_rate": 0.75,
    },
    "hybrid": {
        "precision_at_k": 0.65,
        "ndcg_at_k": 0.72,
    },
    "slice": {
        "lexical": {"row_hit_rate": 0.75},
        "hybrid": {"row_hit_rate": 0.75},
    },
    "semantic_vs_lexical_mrr_delta": 0.05,
    "hybrid_vs_best_single_mrr_tolerance": 0.01,
}


def load_eval_prompts(path: Path) -> list[dict[str, Any]]:
    return eval_load_eval_prompts(path)


def evaluate_retrieval_prompts(
    *,
    db_path: Path,
    contract_path: Path,
    query_log_root: Path,
    prompts: list[dict[str, Any]],
    top_k: int,
    candidate_limit: int,
    allow_degraded: bool,
    semantic_config: SemanticBackendConfig,
    semantic_retries: int,
    enforce_gates: bool = True,
    model_cache_dir: str = "",
    backend_profile: str = "python-local",
    backend_attempt_log_path: Path | None = None,
    root_cause_run_id: str = "",
    root_cause_cell_id: str = "",
    hybrid_fusion_method: str = "weighted-v1",
    hybrid_rrf_k: int = 60,
    hybrid_rrf_window: int = 0,
    hybrid_fusion_routing: str = HYBRID_FUSION_ROUTING_OFF,
    hybrid_lexical_floor_count: int = 0,
    hybrid_lexical_floor_share: float = 0.0,
    hybrid_candidate_policy: str = "legacy",
    hybrid_rerank_pool_size: int = 0,
    hybrid_lexical_min: int = 0,
    hybrid_semantic_min: int = 0,
    suite_id: str = "",
    operation: str = "eval",
    row_projection_policy: RowProjectionPolicy | None = None,
    corpus: str = "rust_reference",
) -> dict[str, Any]:
    case_results: list[dict[str, Any]] = []

    for prompt in prompts:
        for mode in prompt["modes"]:
            case_started = time.perf_counter()
            attempt_context = f"{prompt['prompt_id']}::{mode}"
            case_semantic_config = replace(
                semantic_config,
                attempt_log_path=(
                    str(backend_attempt_log_path)
                    if backend_attempt_log_path is not None
                    else semantic_config.attempt_log_path
                ),
                attempt_context=attempt_context,
                attempt_run_id=str(root_cause_run_id).strip(),
                attempt_cell_id=str(root_cause_cell_id).strip(),
                attempt_prompt_id=str(prompt["prompt_id"]).strip(),
                attempt_mode=str(mode).strip(),
            )
            resolved_hybrid_method, routing_reason = resolve_hybrid_fusion_method_for_case(
                mode=str(mode),
                prompt=prompt,
                default_method=str(hybrid_fusion_method),
                routing_policy=str(hybrid_fusion_routing),
            )
            try:
                query_result = execute_retrieval_query(
                    mode=mode,
                    db_path=db_path,
                    contract_path=contract_path,
                    query_log_root=query_log_root,
                    query_text=prompt["query_text"],
                    row_marker="",
                    top_k=top_k,
                    candidate_limit=candidate_limit,
                    allow_degraded=allow_degraded,
                    semantic_config=case_semantic_config,
                    semantic_retries=semantic_retries,
                    persist_semantic_cache=True,
                    hybrid_fusion_method=resolved_hybrid_method,
                    hybrid_rrf_k=hybrid_rrf_k,
                    hybrid_rrf_window=hybrid_rrf_window,
                    hybrid_lexical_floor_count=hybrid_lexical_floor_count,
                    hybrid_lexical_floor_share=hybrid_lexical_floor_share,
                    hybrid_candidate_policy=hybrid_candidate_policy,
                    hybrid_rerank_pool_size=hybrid_rerank_pool_size,
                    hybrid_lexical_min=hybrid_lexical_min,
                    hybrid_semantic_min=hybrid_semantic_min,
                    row_projection_policy=row_projection_policy,
                    corpus=corpus,
                )
                status = "pass"
                reason = ""
            except ModeExecutionError as exc:
                query_result = {
                    "requested_mode": mode,
                    "executed_mode": mode,
                    "degraded": False,
                    "row_count": 0,
                    "rows": [],
                    "error_code": exc.code,
                    "error": str(exc),
                    "semantic_retry_events": [],
                }
                status = "fail"
                reason = str(exc)

            case_duration_ms = (time.perf_counter() - case_started) * 1000.0

            rows = list(query_result.get("rows", []))
            relevance = [1 if _is_relevant(row, prompt) else 0 for row in rows[:top_k]]
            hard_negative_set = set(prompt["hard_negative_statement_ids"])
            hard_negative_hits = sum(
                1 for row in rows[:top_k] if str(row.get("statement_id", "")) in hard_negative_set
            )

            case_timing = query_result.get("timing")
            if not isinstance(case_timing, dict):
                case_timing = {}
            candidate_generation = query_result.get("candidate_generation")
            if not isinstance(candidate_generation, dict):
                candidate_generation = {}

            case_result = {
                "prompt_id": prompt["prompt_id"],
                "slice": prompt["slice"],
                "mode": mode,
                "semantic_focus": bool(prompt.get("semantic_focus", False)),
                "expect_abstain": bool(prompt.get("expect_abstain", False)),
                "status": status,
                "reason": reason,
                "requested_mode": query_result.get("requested_mode", mode),
                "executed_mode": query_result.get("executed_mode", mode),
                "degraded": bool(query_result.get("degraded", False)),
                "error_code": str(query_result.get("error_code", "")),
                "duration_ms": round(float(query_result.get("duration_ms", case_duration_ms)), 3),
                "total_case_ms": round(
                    float(case_timing.get("total_case_ms", case_duration_ms)), 3
                ),
                "preflight_ms": round(float(case_timing.get("preflight_ms", 0.0)), 3),
                "lexical_ms": round(float(case_timing.get("lexical_ms", 0.0)), 3),
                "semantic_embed_ms": round(float(case_timing.get("semantic_embed_ms", 0.0)), 3),
                "semantic_score_ms": round(float(case_timing.get("semantic_score_ms", 0.0)), 3),
                "rerank_ms": round(float(case_timing.get("rerank_ms", 0.0)), 3),
                "projection_ms": round(float(case_timing.get("projection_ms", 0.0)), 3),
                "lexical_pool_size": int(candidate_generation.get("lexical_pool_size", 0)),
                "semantic_pool_size": int(candidate_generation.get("semantic_pool_size", 0)),
                "union_pool_size": int(candidate_generation.get("union_pool_size", 0)),
                "rerank_pool_size": int(candidate_generation.get("rerank_pool_size", 0)),
                "rerank_doc_count": int(candidate_generation.get("rerank_doc_count", 0)),
                "precision_at_k": round(precision_at_k(relevance, top_k), 6),
                "mrr_at_k": round(mrr_at_k(relevance, top_k), 6),
                "ndcg_at_k": round(ndcg_at_k(relevance, top_k), 6),
                "row_hit_rate": round(
                    row_hit(rows, prompt["expected_row_markers"], top_k),
                    6,
                ),
                "anchor_hit_rate": round(
                    anchor_hit(rows, prompt["relevant_anchor_prefixes"], top_k),
                    6,
                ),
                "hard_negative_rate": round(float(hard_negative_hits) / float(max(1, top_k)), 6),
                "top_statement_ids": [str(row.get("statement_id", "")) for row in rows[:top_k]],
                "semantic_retry_events": list(query_result.get("semantic_retry_events", [])),
                "attempt_context": attempt_context,
                "min_metric_overrides": dict(prompt.get("min_metrics", {}).get(mode, {})),
                "prompt_family": classify_prompt_family(prompt),
                "hybrid_fusion_method_applied": str(resolved_hybrid_method),
                "hybrid_fusion_routing_reason": str(routing_reason),
                "timing": {
                    "preflight_ms": round(float(case_timing.get("preflight_ms", 0.0)), 3),
                    "lexical_ms": round(float(case_timing.get("lexical_ms", 0.0)), 3),
                    "semantic_embed_ms": round(float(case_timing.get("semantic_embed_ms", 0.0)), 3),
                    "semantic_score_ms": round(float(case_timing.get("semantic_score_ms", 0.0)), 3),
                    "rerank_ms": round(float(case_timing.get("rerank_ms", 0.0)), 3),
                    "projection_ms": round(float(case_timing.get("projection_ms", 0.0)), 3),
                    "total_case_ms": round(
                        float(case_timing.get("total_case_ms", case_duration_ms)), 3
                    ),
                },
                "candidate_generation": {
                    "lexical_pool_size": int(candidate_generation.get("lexical_pool_size", 0)),
                    "semantic_pool_size": int(candidate_generation.get("semantic_pool_size", 0)),
                    "union_pool_size": int(candidate_generation.get("union_pool_size", 0)),
                    "rerank_pool_size": int(candidate_generation.get("rerank_pool_size", 0)),
                    "rerank_doc_count": int(candidate_generation.get("rerank_doc_count", 0)),
                },
            }

            row_projection = [
                row for row in list(query_result.get("row_projection", [])) if isinstance(row, dict)
            ]
            predicted_markers = {
                str(row.get("row_marker", "")).strip().lower()
                for row in row_projection
                if str(row.get("row_marker", "")).strip()
            }
            expected_markers = set(prompt["expected_row_markers"])
            projection_precision, projection_recall, projection_f1_score = projection_f1(
                expected_markers,
                predicted_markers,
            )

            abstain_active = bool((query_result.get("abstain") or {}).get("active", False))
            if bool(prompt.get("expect_abstain", False)):
                projection_precision = 1.0 if abstain_active else 0.0
                projection_recall = projection_precision
                projection_f1_score = projection_precision

            case_result["abstain_active"] = abstain_active
            case_result["abstain_reason_code"] = str(
                (query_result.get("abstain") or {}).get("reason_code", "")
            )
            case_result["projection_precision"] = round(float(projection_precision), 6)
            case_result["projection_recall"] = round(float(projection_recall), 6)
            case_result["projection_f1"] = round(float(projection_f1_score), 6)
            case_result["projection_macro_f1"] = case_result["projection_f1"]
            case_result["projected_row_markers"] = sorted(predicted_markers)
            case_result["top_projected_row_marker"] = (
                str(row_projection[0].get("row_marker", "")).strip().lower()
                if row_projection
                else ""
            )
            case_results.append(case_result)

    trace_ids_by_context = _load_trace_ids_by_context(backend_attempt_log_path)
    for case in case_results:
        context = str(case.get("attempt_context", "")).strip()
        trace_ids = list(trace_ids_by_context.get(context, []))
        case["trace_ids"] = trace_ids
        case["trace_id_count"] = int(len(trace_ids))
        case["primary_trace_id"] = str(trace_ids[0]) if trace_ids else ""

    by_mode: dict[str, list[dict[str, Any]]] = {mode: [] for mode in sorted(MODES)}
    by_mode_retrieval: dict[str, list[dict[str, Any]]] = {mode: [] for mode in sorted(MODES)}
    by_slice_mode: dict[str, dict[str, list[dict[str, Any]]]] = {
        slice_name: {mode: [] for mode in sorted(MODES)} for slice_name in sorted(SLICES)
    }
    by_slice_mode_retrieval: dict[str, dict[str, list[dict[str, Any]]]] = {
        slice_name: {mode: [] for mode in sorted(MODES)} for slice_name in sorted(SLICES)
    }
    semantic_focus_by_mode: dict[str, list[dict[str, Any]]] = {mode: [] for mode in sorted(MODES)}
    semantic_focus_by_mode_retrieval: dict[str, list[dict[str, Any]]] = {
        mode: [] for mode in sorted(MODES)
    }
    failures = 0

    for case in case_results:
        by_mode[case["mode"]].append(case)
        by_slice_mode[case["slice"]][case["mode"]].append(case)
        if not bool(case.get("expect_abstain", False)):
            by_mode_retrieval[case["mode"]].append(case)
            by_slice_mode_retrieval[case["slice"]][case["mode"]].append(case)
        if bool(case.get("semantic_focus", False)):
            semantic_focus_by_mode[case["mode"]].append(case)
            if not bool(case.get("expect_abstain", False)):
                semantic_focus_by_mode_retrieval[case["mode"]].append(case)
        if case["status"] == "fail":
            failures += 1

    summary_modes = {mode: aggregate_mode_metrics(rows) for mode, rows in by_mode_retrieval.items()}
    summary_slices = {
        slice_name: {mode: aggregate_mode_metrics(rows) for mode, rows in per_mode.items()}
        for slice_name, per_mode in by_slice_mode_retrieval.items()
    }

    summary_semantic_focus = {
        mode: aggregate_mode_metrics(rows)
        for mode, rows in semantic_focus_by_mode_retrieval.items()
    }
    summary_projection = {
        mode: aggregate_projection_metrics(rows) for mode, rows in by_mode.items()
    }
    summary_durations = {mode: aggregate_duration_metrics(rows) for mode, rows in by_mode.items()}
    summary_durations_failed = {
        mode: aggregate_duration_metrics([case for case in rows if case.get("status") == "fail"])
        for mode, rows in by_mode.items()
    }
    skew_alarms = {
        mode: mode_skew_alarm(
            rows,
            max_row_share_threshold=float(SKEW_THRESHOLDS["max_row_share"]),
            min_row_entropy_threshold=float(SKEW_THRESHOLDS["min_row_entropy"]),
            min_abstain_rate_with_expected_threshold=float(
                SKEW_THRESHOLDS["min_abstain_rate_with_expected"]
            ),
        )
        for mode, rows in by_mode.items()
    }

    degraded_counts = {
        mode: sum(1 for case in rows if bool(case.get("degraded", False)))
        for mode, rows in by_mode.items()
    }

    retry_summary_by_mode: dict[str, dict[str, int]] = {}
    for mode, rows in by_mode.items():
        retry_events = [
            event
            for case in rows
            for event in case.get("semantic_retry_events", [])
            if isinstance(event, dict)
        ]
        total_retry_count = sum(int(event.get("retry_count", 0)) for event in retry_events)
        failure_events = sum(1 for event in retry_events if event.get("status") == "fail")
        retry_summary_by_mode[mode] = {
            "event_count": len(retry_events),
            "total_retry_count": total_retry_count,
            "failure_event_count": failure_events,
        }

    gate_failures: list[str] = []
    if enforce_gates:
        if by_mode_retrieval.get("lexical"):
            lexical_metrics = summary_modes.get("lexical", {})
            for metric, threshold in THRESHOLDS["lexical"].items():
                if float(lexical_metrics.get(metric, 0.0)) < float(threshold):
                    gate_failures.append(
                        "lexical."
                        f"{metric} below threshold: "
                        f"{lexical_metrics.get(metric, 0.0)} < {threshold}"
                    )

        if semantic_focus_by_mode_retrieval.get("semantic"):
            semantic_focus_metrics = summary_semantic_focus.get("semantic", {})
            for metric, threshold in THRESHOLDS["semantic_focus"].items():
                if float(semantic_focus_metrics.get(metric, 0.0)) < float(threshold):
                    gate_failures.append(
                        "semantic.semantic_focus."
                        f"{metric} below threshold: "
                        f"{semantic_focus_metrics.get(metric, 0.0)} < {threshold}"
                    )

        if by_mode_retrieval.get("hybrid"):
            hybrid_metrics = summary_modes.get("hybrid", {})
            for metric, threshold in THRESHOLDS["hybrid"].items():
                if float(hybrid_metrics.get(metric, 0.0)) < float(threshold):
                    gate_failures.append(
                        "hybrid."
                        f"{metric} below threshold: "
                        f"{hybrid_metrics.get(metric, 0.0)} < {threshold}"
                    )

        if by_mode_retrieval.get("hybrid") and (
            by_mode_retrieval.get("lexical") or by_mode_retrieval.get("semantic")
        ):
            hybrid_mrr = float(summary_modes["hybrid"].get("mrr_at_k", 0.0))
            best_single_mrr = max(
                float(summary_modes.get("lexical", {}).get("mrr_at_k", 0.0)),
                float(summary_modes.get("semantic", {}).get("mrr_at_k", 0.0)),
            )
            tolerance = float(THRESHOLDS["hybrid_vs_best_single_mrr_tolerance"])
            if hybrid_mrr + tolerance < best_single_mrr:
                gate_failures.append(
                    "hybrid_vs_best_single overall mrr below tolerance: "
                    f"{hybrid_mrr} + {tolerance} < {best_single_mrr}"
                )

        semantic_focus_semantic_cases = semantic_focus_by_mode_retrieval.get("semantic", [])
        semantic_focus_lexical_cases = semantic_focus_by_mode_retrieval.get("lexical", [])
        semantic_focus_semantic_degraded = any(
            bool(case.get("degraded", False)) for case in semantic_focus_semantic_cases
        )

        if (
            semantic_focus_semantic_cases
            and semantic_focus_lexical_cases
            and not semantic_focus_semantic_degraded
        ):
            semantic_mrr = float(summary_semantic_focus["semantic"].get("mrr_at_k", 0.0))
            lexical_mrr = float(summary_semantic_focus["lexical"].get("mrr_at_k", 0.0))
            required_delta = float(THRESHOLDS["semantic_vs_lexical_mrr_delta"])
            if semantic_mrr < lexical_mrr + required_delta:
                gate_failures.append(
                    "semantic_vs_lexical mrr delta below threshold: "
                    f"{semantic_mrr} < {lexical_mrr} + {required_delta}"
                )

        for slice_name, per_mode in by_slice_mode_retrieval.items():
            if not any(per_mode.get(mode) for mode in MODES):
                continue

            for mode, metric_thresholds in THRESHOLDS["slice"].items():
                if not per_mode.get(mode):
                    continue
                mode_metrics = summary_slices[slice_name][mode]
                for metric, threshold in metric_thresholds.items():
                    if float(mode_metrics.get(metric, 0.0)) < float(threshold):
                        gate_failures.append(
                            f"slice.{slice_name}.{mode}.{metric} below threshold: "
                            f"{mode_metrics.get(metric, 0.0)} < {threshold}"
                        )

            if per_mode.get("hybrid") and (per_mode.get("lexical") or per_mode.get("semantic")):
                hybrid_mrr = float(summary_slices[slice_name]["hybrid"].get("mrr_at_k", 0.0))
                best_single_mrr = max(
                    float(summary_slices[slice_name]["lexical"].get("mrr_at_k", 0.0)),
                    float(summary_slices[slice_name]["semantic"].get("mrr_at_k", 0.0)),
                )
                tolerance = float(THRESHOLDS["hybrid_vs_best_single_mrr_tolerance"])
                if hybrid_mrr + tolerance < best_single_mrr:
                    gate_failures.append(
                        "slice."
                        f"{slice_name}.hybrid_vs_best_single mrr below tolerance: "
                        f"{hybrid_mrr} + {tolerance} < {best_single_mrr}"
                    )

        for case in case_results:
            if case.get("status") != "pass":
                continue
            overrides = case.get("min_metric_overrides", {})
            if not isinstance(overrides, dict):
                continue
            for metric, threshold in overrides.items():
                observed = float(case.get(metric, 0.0))
                if observed < float(threshold):
                    gate_failures.append(
                        "prompt_override."
                        f"{case.get('prompt_id')}.{case.get('mode')}.{metric} below threshold: "
                        f"{observed} < {threshold}"
                    )

    total_failures = failures + len(gate_failures)

    provenance = _load_build_provenance(db_path)

    report = {
        "suite_id": str(suite_id).strip() or "retrieval_eval_v1",
        "operation": str(operation).strip() or "eval",
        "checked_at": _utc_now(),
        "inputs": {
            "db_path": str(db_path),
            "contract_path": str(contract_path),
            "top_k": int(top_k),
            "candidate_limit": int(candidate_limit),
            "hybrid_fusion_method": str(hybrid_fusion_method).strip(),
            "hybrid_rrf_k": int(hybrid_rrf_k),
            "hybrid_rrf_window": int(hybrid_rrf_window),
            "hybrid_fusion_routing": str(hybrid_fusion_routing).strip(),
            "hybrid_lexical_floor_count": int(hybrid_lexical_floor_count),
            "hybrid_lexical_floor_share": float(hybrid_lexical_floor_share),
            "hybrid_candidate_policy": str(hybrid_candidate_policy).strip(),
            "hybrid_rerank_pool_size": int(hybrid_rerank_pool_size),
            "hybrid_lexical_min": int(hybrid_lexical_min),
            "hybrid_semantic_min": int(hybrid_semantic_min),
            "backend_attempt_log_path": (
                str(backend_attempt_log_path) if backend_attempt_log_path is not None else ""
            ),
        },
        "backend": {
            "profile": str(backend_profile).strip(),
            "base_url": str(semantic_config.base_url),
            "embed_base_url": resolve_embed_base_url(semantic_config),
            "rerank_base_url": resolve_rerank_base_url(semantic_config),
            "embed_model_id": str(semantic_config.embed_model_id),
            "reranker_model_id": str(semantic_config.reranker_model_id),
            "model_cache_dir": str(model_cache_dir).strip(),
        },
        "summary": {
            "prompt_count": len(prompts),
            "total_mode_cases": len(case_results),
            "failed_cases": total_failures,
            "degraded_mode_cases": degraded_counts,
            "retry_summary_by_mode": retry_summary_by_mode,
            "modes": summary_modes,
            "slices": summary_slices,
            "semantic_focus": summary_semantic_focus,
            "projection": summary_projection,
            "durations": summary_durations,
            "durations_failed": summary_durations_failed,
            "skew_alarms": skew_alarms,
        },
        "provenance": provenance,
        "gate_failures": gate_failures,
        "cases": case_results,
    }
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate lexical/semantic/hybrid retrieval quality for rust_reference.sqlite"
    )
    parser.add_argument(
        "--corpus",
        choices=list_supported_corpora(),
        default="rust_reference",
        help="Corpus adapter used to resolve default DB/contract paths",
    )
    parser.add_argument(
        "--operation",
        choices=("eval", "verify"),
        default="eval",
        help="Operation tag included in eval report payload",
    )
    parser.add_argument(
        "--db-path",
        default="",
        help="Path to corpus sqlite database (defaults from --corpus)",
    )
    parser.add_argument(
        "--contract-path",
        default="",
        help="Path to query contract file",
    )
    parser.add_argument(
        "--eval-path",
        default="",
        help="Path to retrieval eval prompt definitions (defaults from --corpus)",
    )
    parser.add_argument(
        "--retrieval-profile-path",
        default="",
        help="Optional retrieval profile YAML for model/fusion defaults",
    )
    parser.add_argument(
        "--query-log-root",
        default="",
        help="Directory for query audit logs",
    )
    parser.add_argument(
        "--report-path",
        default=None,
        help="Optional output path for evaluation report",
    )
    parser.add_argument(
        "--backend-attempt-log-path",
        default=None,
        help="Optional JSONL path for semantic/rerank backend attempt traces",
    )
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--candidate-limit", type=int, default=DEFAULT_CANDIDATE_LIMIT)
    parser.add_argument(
        "--hybrid-fusion-method",
        choices=("weighted-v1", "weighted-v2", "rrf-v1"),
        default="weighted-v1",
    )
    parser.add_argument(
        "--hybrid-fusion-routing",
        choices=(HYBRID_FUSION_ROUTING_OFF, HYBRID_FUSION_ROUTING_BEST_PRACTICE_V1),
        default=HYBRID_FUSION_ROUTING_OFF,
        help="Optional routing policy that overrides hybrid fusion method per prompt family",
    )
    parser.add_argument(
        "--hybrid-rrf-k",
        type=int,
        default=60,
    )
    parser.add_argument(
        "--hybrid-rrf-window",
        type=int,
        default=0,
        help="Optional rank window for RRF (0 means auto)",
    )
    parser.add_argument(
        "--hybrid-lexical-floor-count",
        type=int,
        default=0,
        help="Minimum lexical candidates to include in hybrid reranker pool",
    )
    parser.add_argument(
        "--hybrid-lexical-floor-share",
        type=float,
        default=0.0,
        help="Minimum lexical share of hybrid reranker window [0,1]",
    )
    parser.add_argument(
        "--hybrid-candidate-policy",
        choices=("legacy", "v2"),
        default="legacy",
        help="Hybrid candidate assembly policy before fusion",
    )
    parser.add_argument(
        "--hybrid-rerank-pool-size",
        type=int,
        default=0,
        help="Hybrid rerank pool target size (0 means auto)",
    )
    parser.add_argument(
        "--hybrid-lexical-min",
        type=int,
        default=0,
        help="Minimum lexical candidates in hybrid rerank pool when policy=v2",
    )
    parser.add_argument(
        "--hybrid-semantic-min",
        type=int,
        default=0,
        help="Minimum semantic candidates in hybrid rerank pool when policy=v2",
    )
    parser.add_argument("--allow-degraded", action="store_true")
    parser.add_argument(
        "--semantic-base-url",
        default="http://127.0.0.1:8080",
    )
    parser.add_argument(
        "--semantic-embed-base-url",
        default="http://127.0.0.1:8080",
    )
    parser.add_argument(
        "--semantic-rerank-base-url",
        default="http://127.0.0.1:8081",
    )
    parser.add_argument(
        "--embed-model-id",
        default="Qwen/Qwen3-Embedding-4B",
    )
    parser.add_argument(
        "--reranker-model-id",
        default="BAAI/bge-reranker-v2-m3",
    )
    parser.add_argument(
        "--semantic-timeout-sec",
        type=float,
        default=60.0,
    )
    parser.add_argument("--semantic-retries", type=int, default=0)
    parser.add_argument(
        "--enforce-gates",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Apply threshold gates to aggregate retrieval metrics",
    )
    parser.add_argument(
        "--auto-start-local-backend",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Start local backend if semantic preflight is unavailable",
    )
    parser.add_argument(
        "--keep-local-backend-running",
        action="store_true",
        help="Do not stop locally-started backend after evaluation",
    )
    parser.add_argument(
        "--local-embed-device",
        choices=("auto", "cpu", "mps", "cuda"),
        default="auto",
    )
    parser.add_argument(
        "--local-rerank-device",
        choices=("auto", "cpu", "mps", "cuda"),
        default="auto",
    )
    parser.add_argument(
        "--local-model-cache-dir",
        default=".cache/sqlite_kb/models/hf",
    )
    parser.add_argument("--local-startup-timeout-sec", type=float, default=180.0)
    parser.add_argument(
        "--allow-provenance-mismatch",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[3]
    corpus = str(args.corpus).strip().lower()

    retrieval_profile_path = str(args.retrieval_profile_path).strip()
    if retrieval_profile_path:
        profile_path = Path(retrieval_profile_path)
        if not profile_path.is_absolute():
            profile_path = (root / profile_path).resolve()
        profile = load_retrieval_profile(profile_path)
        corpus = enforce_profile_corpus(corpus, profile)
        apply_profile_defaults(args, profile)

    runtime_paths = resolve_corpus_runtime_paths(
        root=root,
        corpus=corpus,
        db_path=str(args.db_path),
        contract_path=str(args.contract_path),
        query_log_root=str(args.query_log_root),
        rewrite_rules_path="",
    )

    db_path = runtime_paths.db_path
    contract_path = runtime_paths.contract_path
    eval_path_raw = str(args.eval_path).strip() or str(
        get_corpus_adapter(corpus).config.default_eval_path
    )
    eval_path = (root / eval_path_raw).resolve()
    query_log_root = runtime_paths.query_log_root
    report_path = (
        (root / args.report_path).resolve()
        if args.report_path
        else runtime_paths.report_root
        / f"retrieval_eval_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    root_cause_run_id, root_cause_cell_id = eval_infer_root_cause_run_and_cell(report_path)

    global THRESHOLDS
    global SKEW_THRESHOLDS
    policy_path = (root / str(get_corpus_adapter(corpus).config.default_eval_policy_path)).resolve()
    if policy_path.exists():
        policy = load_eval_policy(policy_path)
        loaded_thresholds = policy.get("thresholds")
        loaded_skew = policy.get("skew_thresholds")
        if isinstance(loaded_thresholds, dict):
            THRESHOLDS = loaded_thresholds
        if isinstance(loaded_skew, dict):
            SKEW_THRESHOLDS = {str(key): float(value) for key, value in loaded_skew.items()}
    backend_attempt_log_path = (
        (root / args.backend_attempt_log_path).resolve() if args.backend_attempt_log_path else None
    )
    row_projection_policy = resolve_row_projection_policy(root=root, corpus=corpus)

    if backend_attempt_log_path is not None:
        backend_attempt_log_path.parent.mkdir(parents=True, exist_ok=True)
        if backend_attempt_log_path.exists():
            backend_attempt_log_path.unlink()

    semantic_config = SemanticBackendConfig(
        base_url=str(args.semantic_base_url),
        embed_model_id=str(args.embed_model_id),
        reranker_model_id=str(args.reranker_model_id),
        timeout_sec=float(args.semantic_timeout_sec),
        embed_base_url=(str(args.semantic_embed_base_url).strip() or None),
        rerank_base_url=(str(args.semantic_rerank_base_url).strip() or None),
    )

    started_local_backend = False
    if bool(args.auto_start_local_backend):
        preflight = check_semantic_backend(semantic_config)
        if not bool(preflight.get("ok", False)):
            start_command = [
                sys.executable,
                str(root / "scripts/sqlite_local_semantic_backend.py"),
                "start",
                "--embed-base-url",
                resolve_embed_base_url(semantic_config),
                "--rerank-base-url",
                resolve_rerank_base_url(semantic_config),
                "--embed-model-id",
                str(args.embed_model_id),
                "--rerank-model-id",
                str(args.reranker_model_id),
                "--embed-device",
                str(args.local_embed_device),
                "--rerank-device",
                str(args.local_rerank_device),
                "--model-cache-dir",
                str(args.local_model_cache_dir),
                "--startup-timeout-sec",
                str(args.local_startup_timeout_sec),
            ]
            completed = subprocess.run(start_command, check=False)
            if completed.returncode != 0:
                print(
                    "[eval-rust-reference-retrieval][error] "
                    "failed to auto-start local semantic backend"
                )
                return EXIT_RUNTIME_FAIL
            started_local_backend = True

    try:
        prompts = eval_load_eval_prompts(eval_path)
        eval_payload = _load_yaml(eval_path)
        suite_id = str(eval_payload.get("suite_id", "")).strip() or eval_path.stem
        if suite_id.startswith("core_docs_"):
            _validate_required_evidence_fields(db_path, prompts)
        report = evaluate_retrieval_prompts(
            db_path=db_path,
            contract_path=contract_path,
            query_log_root=query_log_root,
            prompts=prompts,
            top_k=int(args.top_k),
            candidate_limit=int(args.candidate_limit),
            allow_degraded=bool(args.allow_degraded),
            semantic_config=semantic_config,
            semantic_retries=int(args.semantic_retries),
            enforce_gates=bool(args.enforce_gates),
            model_cache_dir=str(args.local_model_cache_dir),
            backend_profile="python-local",
            backend_attempt_log_path=backend_attempt_log_path,
            root_cause_run_id=root_cause_run_id,
            root_cause_cell_id=root_cause_cell_id,
            hybrid_fusion_method=str(args.hybrid_fusion_method),
            hybrid_rrf_k=int(args.hybrid_rrf_k),
            hybrid_rrf_window=int(args.hybrid_rrf_window),
            hybrid_fusion_routing=str(args.hybrid_fusion_routing),
            hybrid_lexical_floor_count=int(args.hybrid_lexical_floor_count),
            hybrid_lexical_floor_share=float(args.hybrid_lexical_floor_share),
            hybrid_candidate_policy=str(args.hybrid_candidate_policy),
            hybrid_rerank_pool_size=int(args.hybrid_rerank_pool_size),
            hybrid_lexical_min=int(args.hybrid_lexical_min),
            hybrid_semantic_min=int(args.hybrid_semantic_min),
            suite_id=suite_id,
            operation=str(args.operation),
            row_projection_policy=row_projection_policy,
            corpus=corpus,
        )
    except (RuntimeError, GuardrailError, OSError) as exc:
        print(f"[eval-rust-reference-retrieval][error] {exc}")
        return EXIT_RUNTIME_FAIL
    finally:
        if started_local_backend and not bool(args.keep_local_backend_running):
            subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts/sqlite_local_semantic_backend.py"),
                    "stop",
                ],
                check=False,
            )

    write_eval_report(report_path, report)

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"[eval-rust-reference-retrieval] report -> {report_path}")
    if int(report["summary"]["failed_cases"]) > 0:
        print("[eval-rust-reference-retrieval][error] Retrieval evaluation failures detected")
        return EXIT_RUNTIME_FAIL
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
