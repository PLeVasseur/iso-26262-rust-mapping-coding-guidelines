#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from semantic_backend_client import (
    SemanticBackendConfig,
    check_semantic_backend,
    resolve_embed_base_url,
    resolve_rerank_base_url,
)
from sqlite_query_guardrails import GuardrailError
from sqlite_query_rust_reference import ModeExecutionError, execute_retrieval_query

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3
DEFAULT_TOP_K = 10
DEFAULT_CANDIDATE_LIMIT = 5000
SLICES = {"issue_identification", "resolution_identification"}
MODES = {"lexical", "semantic", "hybrid"}
ROW_MARKERS = tuple(f"1{chr(ord('a') + idx)}" for idx in range(9))
OVERRIDABLE_MIN_METRICS = {
    "precision_at_k",
    "mrr_at_k",
    "ndcg_at_k",
    "row_hit_rate",
}
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

HYBRID_FUSION_ROUTING_OFF = "off"
HYBRID_FUSION_ROUTING_BEST_PRACTICE_V1 = "best-practice-v1"


def _classify_prompt_family(prompt: dict[str, Any]) -> str:
    prompt_id = str(prompt.get("prompt_id", "")).strip().lower()
    query_text = str(prompt.get("query_text", "")).strip().lower()
    relevant_terms = " ".join(
        str(term).strip().lower() for term in prompt.get("relevant_terms", [])
    )
    haystack = " ".join(part for part in (prompt_id, query_text, relevant_terms) if part)

    unsafe_control_flow_tokens = (
        "unsafe",
        "undefined",
        "pointer",
        "alias",
        "lifetime",
        "control-flow",
        "control flow",
        "pattern",
        "match",
        "branch",
    )
    error_handling_tokens = (
        "error",
        "panic",
        "unwrap",
        "expect",
        "result",
        "defensive",
    )

    if any(token in haystack for token in unsafe_control_flow_tokens):
        return "unsafe_control_flow"
    if any(token in haystack for token in error_handling_tokens):
        return "error_handling"
    return "general_semantics"


def _resolve_hybrid_fusion_method_for_case(
    *,
    mode: str,
    prompt: dict[str, Any],
    default_method: str,
    routing_policy: str,
) -> tuple[str, str]:
    if str(mode).strip() != "hybrid":
        return str(default_method).strip(), "not_hybrid"

    normalized_policy = str(routing_policy).strip().lower() or HYBRID_FUSION_ROUTING_OFF
    if normalized_policy == HYBRID_FUSION_ROUTING_OFF:
        return str(default_method).strip(), "routing_off"

    prompt_family = _classify_prompt_family(prompt)
    if normalized_policy == HYBRID_FUSION_ROUTING_BEST_PRACTICE_V1:
        if prompt_family == "unsafe_control_flow":
            return "weighted-v2", prompt_family
        return "rrf-v1", prompt_family

    return str(default_method).strip(), "unknown_routing_policy"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _split_csv(raw: str) -> set[str]:
    return {token.strip().lower() for token in str(raw).split(",") if token.strip()}


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"YAML payload at {path} must be a mapping")
    return payload


def load_eval_prompts(path: Path) -> list[dict[str, Any]]:
    payload = _load_yaml(path)
    prompts = payload.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        raise RuntimeError("Retrieval eval file must define non-empty prompts list")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_prompt in prompts:
        if not isinstance(raw_prompt, dict):
            raise RuntimeError("Each prompt entry must be a mapping")

        prompt_id = str(raw_prompt.get("prompt_id", "")).strip()
        if not prompt_id:
            raise RuntimeError("Prompt missing prompt_id")
        if prompt_id in seen_ids:
            raise RuntimeError(f"Duplicate prompt_id: {prompt_id}")
        seen_ids.add(prompt_id)

        slice_name = str(raw_prompt.get("slice", "")).strip()
        if slice_name not in SLICES:
            raise RuntimeError(f"Prompt {prompt_id} has invalid slice {slice_name}")

        query_text = str(raw_prompt.get("query_text", "")).strip()
        if not query_text:
            raise RuntimeError(f"Prompt {prompt_id} missing query_text")

        raw_modes = raw_prompt.get("modes")
        if not isinstance(raw_modes, list) or not raw_modes:
            raise RuntimeError(f"Prompt {prompt_id} must define non-empty modes")
        modes = [str(mode).strip() for mode in raw_modes]
        if any(mode not in MODES for mode in modes):
            raise RuntimeError(f"Prompt {prompt_id} includes unknown mode")

        expected_row_markers = [
            str(value).strip().lower()
            for value in (raw_prompt.get("expected_row_markers") or [])
            if str(value).strip()
        ]
        relevant_statement_ids = [
            str(value).strip() for value in (raw_prompt.get("relevant_statement_ids") or [])
        ]
        relevant_anchor_prefixes = [
            str(value).strip() for value in (raw_prompt.get("relevant_anchor_prefixes") or [])
        ]
        relevant_terms = [
            str(value).strip().lower() for value in (raw_prompt.get("relevant_terms") or [])
        ]
        hard_negative_statement_ids = [
            str(value).strip() for value in (raw_prompt.get("hard_negative_statement_ids") or [])
        ]
        raw_min_metrics = raw_prompt.get("min_metrics") or {}
        if not isinstance(raw_min_metrics, dict):
            raise RuntimeError(f"Prompt {prompt_id} min_metrics must be a mapping")

        min_metrics: dict[str, dict[str, float]] = {}
        for mode_name, metric_mapping in raw_min_metrics.items():
            mode_key = str(mode_name).strip()
            if mode_key not in MODES:
                raise RuntimeError(f"Prompt {prompt_id} min_metrics has unknown mode {mode_key}")
            if not isinstance(metric_mapping, dict):
                raise RuntimeError(
                    f"Prompt {prompt_id} min_metrics for {mode_key} must be a mapping"
                )

            normalized_metrics: dict[str, float] = {}
            for metric_name, metric_value in metric_mapping.items():
                metric_key = str(metric_name).strip()
                if metric_key not in OVERRIDABLE_MIN_METRICS:
                    raise RuntimeError(
                        f"Prompt {prompt_id} min_metrics has unsupported metric {metric_key}"
                    )
                normalized_metrics[metric_key] = float(metric_value)
            if normalized_metrics:
                min_metrics[mode_key] = normalized_metrics

        if not (
            expected_row_markers
            or relevant_statement_ids
            or relevant_anchor_prefixes
            or relevant_terms
        ):
            raise RuntimeError(
                f"Prompt {prompt_id} must define at least one relevance signal "
                "(row markers, statement ids, anchor prefixes, or terms)"
            )

        normalized.append(
            {
                "prompt_id": prompt_id,
                "slice": slice_name,
                "query_text": query_text,
                "modes": modes,
                "expected_row_markers": expected_row_markers,
                "relevant_statement_ids": relevant_statement_ids,
                "relevant_anchor_prefixes": relevant_anchor_prefixes,
                "relevant_terms": relevant_terms,
                "hard_negative_statement_ids": hard_negative_statement_ids,
                "min_metrics": min_metrics,
                "semantic_focus": bool(raw_prompt.get("semantic_focus", False)),
                "expect_abstain": bool(raw_prompt.get("expect_abstain", False)),
            }
        )

    return normalized


def _is_relevant(row: dict[str, Any], prompt: dict[str, Any]) -> bool:
    statement_id = str(row.get("statement_id", ""))
    source_anchor = str(row.get("source_anchor", ""))
    row_markers = {value.lower() for value in row.get("row_markers", [])}
    statement_text = str(row.get("statement_text", "")).lower()

    if statement_id and statement_id in set(prompt["relevant_statement_ids"]):
        return True

    anchor_prefixes = [prefix for prefix in prompt["relevant_anchor_prefixes"] if prefix]
    if anchor_prefixes and any(source_anchor.startswith(prefix) for prefix in anchor_prefixes):
        return True

    expected_rows = set(prompt["expected_row_markers"])
    row_match = bool(expected_rows.intersection(row_markers)) if expected_rows else True

    relevant_terms = [term for term in prompt["relevant_terms"] if term]
    if relevant_terms:
        term_match = any(term in statement_text for term in relevant_terms)
        if row_match and term_match:
            return True

    if expected_rows and row_match and not relevant_terms and not anchor_prefixes:
        return True

    return False


def _precision_at_k(relevance: list[int], k: int) -> float:
    if k <= 0:
        return 0.0
    observed = relevance[:k]
    if not observed:
        return 0.0
    return float(sum(observed)) / float(len(observed))


def _mrr_at_k(relevance: list[int], k: int) -> float:
    for idx, rel in enumerate(relevance[:k], start=1):
        if rel:
            return 1.0 / float(idx)
    return 0.0


def _ndcg_at_k(relevance: list[int], k: int) -> float:
    def _dcg(values: list[int]) -> float:
        score = 0.0
        for idx, value in enumerate(values, start=1):
            if value <= 0:
                continue
            score += float(value) / math.log2(idx + 1.0)
        return score

    observed = _dcg(relevance[:k])
    ideal = _dcg(sorted(relevance[:k], reverse=True))
    if ideal <= 0.0:
        return 0.0
    return observed / ideal


def _row_hit(rows: list[dict[str, Any]], expected_row_markers: list[str], k: int) -> float:
    if not expected_row_markers:
        return 1.0
    expected = set(expected_row_markers)
    for row in rows[:k]:
        markers = {value.lower() for value in row.get("row_markers", [])}
        if expected.intersection(markers):
            return 1.0
    return 0.0


def _anchor_hit(rows: list[dict[str, Any]], anchor_prefixes: list[str], k: int) -> float:
    prefixes = [prefix for prefix in anchor_prefixes if prefix]
    if not prefixes:
        return 1.0
    for row in rows[:k]:
        source_anchor = str(row.get("source_anchor", ""))
        if any(source_anchor.startswith(prefix) for prefix in prefixes):
            return 1.0
    return 0.0


def _projection_f1(expected: set[str], predicted: set[str]) -> tuple[float, float, float]:
    if not expected and not predicted:
        return 1.0, 1.0, 1.0
    if not predicted:
        return 0.0, 0.0, 0.0

    overlap = expected.intersection(predicted)
    precision = float(len(overlap)) / float(len(predicted))
    recall = 0.0 if not expected else float(len(overlap)) / float(len(expected))
    if precision + recall <= 0.0:
        return precision, recall, 0.0
    return precision, recall, (2.0 * precision * recall) / (precision + recall)


def _aggregate_mode_metrics(case_rows: list[dict[str, Any]]) -> dict[str, float]:
    if not case_rows:
        return {
            "precision_at_k": 0.0,
            "mrr_at_k": 0.0,
            "ndcg_at_k": 0.0,
            "row_hit_rate": 0.0,
            "anchor_hit_rate": 0.0,
            "hard_negative_rate": 0.0,
            "projection_macro_f1": 0.0,
        }

    metric_names = [
        "precision_at_k",
        "mrr_at_k",
        "ndcg_at_k",
        "row_hit_rate",
        "anchor_hit_rate",
        "hard_negative_rate",
        "projection_macro_f1",
    ]
    return {
        name: round(sum(float(case[name]) for case in case_rows) / float(len(case_rows)), 6)
        for name in metric_names
    }


def _aggregate_projection_metrics(case_rows: list[dict[str, Any]]) -> dict[str, float]:
    if not case_rows:
        return {
            "macro_f1": 0.0,
            "macro_precision": 0.0,
            "macro_recall": 0.0,
            "abstain_rate": 0.0,
            "abstain_precision": 0.0,
            "abstain_recall": 0.0,
        }

    macro_f1 = sum(float(case.get("projection_f1", 0.0)) for case in case_rows) / float(
        len(case_rows)
    )
    macro_precision = sum(
        float(case.get("projection_precision", 0.0)) for case in case_rows
    ) / float(len(case_rows))
    macro_recall = sum(float(case.get("projection_recall", 0.0)) for case in case_rows) / float(
        len(case_rows)
    )

    abstain_pred_count = sum(1 for case in case_rows if bool(case.get("abstain_active", False)))
    abstain_expected_count = sum(1 for case in case_rows if bool(case.get("expect_abstain", False)))
    abstain_tp = sum(
        1
        for case in case_rows
        if bool(case.get("abstain_active", False)) and bool(case.get("expect_abstain", False))
    )

    return {
        "macro_f1": round(float(macro_f1), 6),
        "macro_precision": round(float(macro_precision), 6),
        "macro_recall": round(float(macro_recall), 6),
        "abstain_rate": round(float(abstain_pred_count) / float(len(case_rows)), 6),
        "abstain_precision": round(
            float(abstain_tp) / float(abstain_pred_count) if abstain_pred_count > 0 else 0.0,
            6,
        ),
        "abstain_recall": round(
            float(abstain_tp) / float(abstain_expected_count)
            if abstain_expected_count > 0
            else 1.0,
            6,
        ),
    }


def _aggregate_duration_metrics(case_rows: list[dict[str, Any]]) -> dict[str, float]:
    if not case_rows:
        return {
            "total_ms": 0.0,
            "avg_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "max_ms": 0.0,
        }

    durations = sorted(float(case.get("duration_ms", 0.0)) for case in case_rows)
    total = sum(durations)
    count = len(durations)
    p50_index = int((count - 1) * 0.50)
    p95_index = int((count - 1) * 0.95)
    return {
        "total_ms": round(float(total), 3),
        "avg_ms": round(float(total) / float(count), 3),
        "p50_ms": round(float(durations[p50_index]), 3),
        "p95_ms": round(float(durations[p95_index]), 3),
        "max_ms": round(float(durations[-1]), 3),
    }


def _mode_skew_alarm(case_rows: list[dict[str, Any]]) -> dict[str, Any]:
    predicted_markers = [
        str(case.get("top_projected_row_marker", "")).strip().lower()
        for case in case_rows
        if str(case.get("top_projected_row_marker", "")).strip()
    ]
    total = len(predicted_markers)
    distribution = {
        marker: predicted_markers.count(marker) for marker in sorted(set(predicted_markers))
    }

    max_row_share = 0.0
    row_entropy = 0.0
    row_gini = 0.0
    if total > 0:
        probabilities = [float(count) / float(total) for count in distribution.values()]
        max_row_share = max(probabilities)
        row_entropy = -sum(p * math.log2(p) for p in probabilities if p > 0.0)
        max_entropy = math.log2(float(len(ROW_MARKERS)))
        if max_entropy > 0.0:
            row_entropy = row_entropy / max_entropy
        row_gini = 1.0 - sum(p * p for p in probabilities)

    abstain_rate = float(
        sum(1 for case in case_rows if bool(case.get("abstain_active", False)))
    ) / float(max(1, len(case_rows)))
    expected_abstain_count = sum(1 for case in case_rows if bool(case.get("expect_abstain", False)))

    alerts: list[str] = []
    if max_row_share > float(SKEW_THRESHOLDS["max_row_share"]):
        alerts.append(f"max_row_share>{SKEW_THRESHOLDS['max_row_share']}: {max_row_share:.4f}")
    if total > 1 and row_entropy < float(SKEW_THRESHOLDS["min_row_entropy"]):
        alerts.append(f"row_entropy<{SKEW_THRESHOLDS['min_row_entropy']}: {row_entropy:.4f}")
    if expected_abstain_count > 0 and abstain_rate < float(
        SKEW_THRESHOLDS["min_abstain_rate_with_expected"]
    ):
        alerts.append(
            "abstain_rate_collapse: "
            f"{abstain_rate:.4f} < {SKEW_THRESHOLDS['min_abstain_rate_with_expected']}"
        )

    return {
        "max_row_share": round(float(max_row_share), 6),
        "row_entropy": round(float(row_entropy), 6),
        "row_gini": round(float(row_gini), 6),
        "abstain_rate": round(float(abstain_rate), 6),
        "expected_abstain_count": int(expected_abstain_count),
        "predicted_distribution": distribution,
        "alerts": alerts,
    }


def _load_build_provenance(db_path: Path) -> dict[str, Any]:
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return {
            "kb_metadata": {},
            "snapshot": {},
            "counts": {"chunks": 0, "statements": 0, "docs": 0},
        }

    try:
        connection.row_factory = sqlite3.Row
        kb_metadata = connection.execute(
            "SELECT kb_id, source_name, source_revision, extractor_version, "
            "built_at, notes FROM kb_metadata LIMIT 1"
        ).fetchone()
        snapshot = connection.execute(
            "SELECT snapshot_id, commit_sha, source_url, fetched_at, sha256 FROM snapshots LIMIT 1"
        ).fetchone()
        counts = connection.execute(
            "SELECT (SELECT COUNT(*) FROM chunks) AS chunk_count, "
            "(SELECT COUNT(*) FROM statements) AS statement_count, "
            "(SELECT COUNT(*) FROM docs) AS doc_count"
        ).fetchone()
    except sqlite3.Error:
        return {
            "kb_metadata": {},
            "snapshot": {},
            "counts": {"chunks": 0, "statements": 0, "docs": 0},
        }
    finally:
        connection.close()

    return {
        "kb_metadata": dict(kb_metadata) if kb_metadata is not None else {},
        "snapshot": dict(snapshot) if snapshot is not None else {},
        "counts": {
            "chunks": int(counts["chunk_count"]) if counts is not None else 0,
            "statements": int(counts["statement_count"]) if counts is not None else 0,
            "docs": int(counts["doc_count"]) if counts is not None else 0,
        },
    }


def _load_trace_ids_by_context(path: Path | None) -> dict[str, list[str]]:
    if path is None or not path.exists():
        return {}

    by_context: dict[str, list[str]] = {}
    seen_by_context: dict[str, set[str]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        context = str(payload.get("context", "")).strip()
        trace_id = str(payload.get("trace_id", "")).strip()
        if not context or not trace_id:
            continue

        seen = seen_by_context.setdefault(context, set())
        if trace_id in seen:
            continue
        seen.add(trace_id)
        by_context.setdefault(context, []).append(trace_id)

    return by_context


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
            resolved_hybrid_method, routing_reason = _resolve_hybrid_fusion_method_for_case(
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
                "precision_at_k": round(_precision_at_k(relevance, top_k), 6),
                "mrr_at_k": round(_mrr_at_k(relevance, top_k), 6),
                "ndcg_at_k": round(_ndcg_at_k(relevance, top_k), 6),
                "row_hit_rate": round(
                    _row_hit(rows, prompt["expected_row_markers"], top_k),
                    6,
                ),
                "anchor_hit_rate": round(
                    _anchor_hit(rows, prompt["relevant_anchor_prefixes"], top_k),
                    6,
                ),
                "hard_negative_rate": round(float(hard_negative_hits) / float(max(1, top_k)), 6),
                "top_statement_ids": [str(row.get("statement_id", "")) for row in rows[:top_k]],
                "semantic_retry_events": list(query_result.get("semantic_retry_events", [])),
                "attempt_context": attempt_context,
                "min_metric_overrides": dict(prompt.get("min_metrics", {}).get(mode, {})),
                "prompt_family": _classify_prompt_family(prompt),
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
            projection_precision, projection_recall, projection_f1 = _projection_f1(
                expected_markers,
                predicted_markers,
            )

            abstain_active = bool((query_result.get("abstain") or {}).get("active", False))
            if bool(prompt.get("expect_abstain", False)):
                projection_precision = 1.0 if abstain_active else 0.0
                projection_recall = projection_precision
                projection_f1 = projection_precision

            case_result["abstain_active"] = abstain_active
            case_result["abstain_reason_code"] = str(
                (query_result.get("abstain") or {}).get("reason_code", "")
            )
            case_result["projection_precision"] = round(float(projection_precision), 6)
            case_result["projection_recall"] = round(float(projection_recall), 6)
            case_result["projection_f1"] = round(float(projection_f1), 6)
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

    summary_modes = {
        mode: _aggregate_mode_metrics(rows) for mode, rows in by_mode_retrieval.items()
    }
    summary_slices = {
        slice_name: {mode: _aggregate_mode_metrics(rows) for mode, rows in per_mode.items()}
        for slice_name, per_mode in by_slice_mode_retrieval.items()
    }

    summary_semantic_focus = {
        mode: _aggregate_mode_metrics(rows)
        for mode, rows in semantic_focus_by_mode_retrieval.items()
    }
    summary_projection = {
        mode: _aggregate_projection_metrics(rows) for mode, rows in by_mode.items()
    }
    summary_durations = {mode: _aggregate_duration_metrics(rows) for mode, rows in by_mode.items()}
    summary_durations_failed = {
        mode: _aggregate_duration_metrics([case for case in rows if case.get("status") == "fail"])
        for mode, rows in by_mode.items()
    }
    skew_alarms = {mode: _mode_skew_alarm(rows) for mode, rows in by_mode.items()}

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
        "suite_id": "rust_reference_table1_retrieval_eval_v1",
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


def _default_report_path(root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return root / ".cache/sqlite_kb/reports/rust_reference" / f"retrieval_eval_{stamp}.json"


def _infer_root_cause_run_and_cell(report_path: Path) -> tuple[str, str]:
    parts = list(report_path.parts)
    run_id = ""
    cell_id = ""

    if "root_cause" in parts:
        idx = parts.index("root_cause")
        if idx + 1 < len(parts):
            run_id = str(parts[idx + 1]).strip()

    if "matrix" in parts:
        idx = parts.index("matrix")
        if idx + 1 < len(parts):
            cell_id = str(parts[idx + 1]).strip()

    return run_id, cell_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate lexical/semantic/hybrid retrieval quality for rust_reference.sqlite"
    )
    parser.add_argument(
        "--db-path",
        default=".cache/sqlite_kb/current/rust_reference.sqlite",
        help="Path to rust_reference sqlite database",
    )
    parser.add_argument(
        "--contract-path",
        default="config/sqlite_query_contracts/rust_reference_chunk.yaml",
        help="Path to query contract file",
    )
    parser.add_argument(
        "--eval-path",
        default="data/query_testsets/rust_reference_table1_retrieval_eval.yaml",
        help="Path to retrieval eval prompt definitions",
    )
    parser.add_argument(
        "--query-log-root",
        default=".cache/sqlite_kb/query_logs/rust_reference",
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
        default=os.environ.get("RUST_REF_HYBRID_FUSION_METHOD", "weighted-v1"),
    )
    parser.add_argument(
        "--hybrid-fusion-routing",
        choices=(HYBRID_FUSION_ROUTING_OFF, HYBRID_FUSION_ROUTING_BEST_PRACTICE_V1),
        default=os.environ.get("RUST_REF_HYBRID_FUSION_ROUTING", HYBRID_FUSION_ROUTING_OFF),
        help="Optional routing policy that overrides hybrid fusion method per prompt family",
    )
    parser.add_argument(
        "--hybrid-rrf-k",
        type=int,
        default=int(os.environ.get("RUST_REF_HYBRID_RRF_K", "60")),
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
        default=int(os.environ.get("RUST_REF_HYBRID_LEXICAL_FLOOR_COUNT", "0")),
        help="Minimum lexical candidates to include in hybrid reranker pool",
    )
    parser.add_argument(
        "--hybrid-lexical-floor-share",
        type=float,
        default=float(os.environ.get("RUST_REF_HYBRID_LEXICAL_FLOOR_SHARE", "0.0")),
        help="Minimum lexical share of hybrid reranker window [0,1]",
    )
    parser.add_argument(
        "--hybrid-candidate-policy",
        choices=("legacy", "v2"),
        default=os.environ.get("RUST_REF_HYBRID_CANDIDATE_POLICY", "legacy"),
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
        default=os.environ.get("RUST_REF_TEI_BASE_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument(
        "--semantic-embed-base-url",
        default=os.environ.get("RUST_REF_TEI_EMBED_BASE_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument(
        "--semantic-rerank-base-url",
        default=os.environ.get("RUST_REF_TEI_RERANK_BASE_URL", "http://127.0.0.1:8081"),
    )
    parser.add_argument(
        "--embed-model-id",
        default=os.environ.get("RUST_REF_EMBED_MODEL_ID", "Qwen/Qwen3-Embedding-4B"),
    )
    parser.add_argument(
        "--reranker-model-id",
        default=os.environ.get("RUST_REF_RERANK_MODEL_ID", "BAAI/bge-reranker-v2-m3"),
    )
    parser.add_argument(
        "--semantic-timeout-sec",
        type=float,
        default=float(os.environ.get("RUST_REF_SEMANTIC_TIMEOUT_SEC", "60.0")),
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
        default=os.environ.get("RUST_REF_LOCAL_EMBED_DEVICE", "auto"),
    )
    parser.add_argument(
        "--local-rerank-device",
        choices=("auto", "cpu", "mps", "cuda"),
        default=os.environ.get("RUST_REF_LOCAL_RERANK_DEVICE", "auto"),
    )
    parser.add_argument(
        "--local-model-cache-dir",
        default=os.environ.get(
            "RUST_REF_SEMANTIC_MODEL_CACHE_DIR",
            os.environ.get("RUST_REF_TEI_MODEL_CACHE_DIR", ".cache/sqlite_kb/models/hf"),
        ),
    )
    parser.add_argument("--local-startup-timeout-sec", type=float, default=180.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]

    db_path = (root / args.db_path).resolve()
    contract_path = (root / args.contract_path).resolve()
    eval_path = (root / args.eval_path).resolve()
    query_log_root = (root / args.query_log_root).resolve()
    report_path = (
        (root / args.report_path).resolve() if args.report_path else _default_report_path(root)
    )
    root_cause_run_id, root_cause_cell_id = _infer_root_cause_run_and_cell(report_path)
    backend_attempt_log_path = (
        (root / args.backend_attempt_log_path).resolve() if args.backend_attempt_log_path else None
    )

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
        prompts = load_eval_prompts(eval_path)
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

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"[eval-rust-reference-retrieval] report -> {report_path}")
    if int(report["summary"]["failed_cases"]) > 0:
        print("[eval-rust-reference-retrieval][error] Retrieval evaluation failures detected")
        return EXIT_RUNTIME_FAIL
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
