#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import subprocess
import sys
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

    macro_f1 = sum(float(case.get("projection_f1", 0.0)) for case in case_rows) / float(len(case_rows))
    macro_precision = sum(float(case.get("projection_precision", 0.0)) for case in case_rows) / float(
        len(case_rows)
    )
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
            float(abstain_tp) / float(abstain_expected_count) if abstain_expected_count > 0 else 1.0,
            6,
        ),
    }


def _mode_skew_alarm(case_rows: list[dict[str, Any]]) -> dict[str, Any]:
    predicted_markers = [
        str(case.get("top_projected_row_marker", "")).strip().lower()
        for case in case_rows
        if str(case.get("top_projected_row_marker", "")).strip()
    ]
    total = len(predicted_markers)
    distribution = {
        marker: predicted_markers.count(marker)
        for marker in sorted(set(predicted_markers))
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

    abstain_rate = (
        float(sum(1 for case in case_rows if bool(case.get("abstain_active", False))))
        / float(max(1, len(case_rows)))
    )
    expected_abstain_count = sum(1 for case in case_rows if bool(case.get("expect_abstain", False)))

    alerts: list[str] = []
    if max_row_share > float(SKEW_THRESHOLDS["max_row_share"]):
        alerts.append(
            f"max_row_share>{SKEW_THRESHOLDS['max_row_share']}: {max_row_share:.4f}"
        )
    if total > 1 and row_entropy < float(SKEW_THRESHOLDS["min_row_entropy"]):
        alerts.append(
            f"row_entropy<{SKEW_THRESHOLDS['min_row_entropy']}: {row_entropy:.4f}"
        )
    if expected_abstain_count > 0 and abstain_rate < float(SKEW_THRESHOLDS["min_abstain_rate_with_expected"]):
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
            "SELECT kb_id, source_name, source_revision, extractor_version, built_at, notes FROM kb_metadata LIMIT 1"
        ).fetchone()
        snapshot = connection.execute(
            "SELECT snapshot_id, commit_sha, source_url, fetched_at, sha256 FROM snapshots LIMIT 1"
        ).fetchone()
        counts = connection.execute(
            "SELECT (SELECT COUNT(*) FROM chunks) AS chunk_count, (SELECT COUNT(*) FROM statements) AS statement_count, (SELECT COUNT(*) FROM docs) AS doc_count"
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
) -> dict[str, Any]:
    case_results: list[dict[str, Any]] = []

    for prompt in prompts:
        for mode in prompt["modes"]:
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
                    semantic_config=semantic_config,
                    semantic_retries=semantic_retries,
                    persist_semantic_cache=True,
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

            rows = list(query_result.get("rows", []))
            relevance = [1 if _is_relevant(row, prompt) else 0 for row in rows[:top_k]]
            hard_negative_set = set(prompt["hard_negative_statement_ids"])
            hard_negative_hits = sum(
                1
                for row in rows[:top_k]
                if str(row.get("statement_id", "")) in hard_negative_set
            )

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
                "min_metric_overrides": dict(prompt.get("min_metrics", {}).get(mode, {})),
            }

            row_projection = [
                row
                for row in list(query_result.get("row_projection", []))
                if isinstance(row, dict)
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

    by_mode: dict[str, list[dict[str, Any]]] = {mode: [] for mode in sorted(MODES)}
    by_mode_retrieval: dict[str, list[dict[str, Any]]] = {mode: [] for mode in sorted(MODES)}
    by_slice_mode: dict[str, dict[str, list[dict[str, Any]]]] = {
        slice_name: {mode: [] for mode in sorted(MODES)} for slice_name in sorted(SLICES)
    }
    by_slice_mode_retrieval: dict[str, dict[str, list[dict[str, Any]]]] = {
        slice_name: {mode: [] for mode in sorted(MODES)} for slice_name in sorted(SLICES)
    }
    semantic_focus_by_mode: dict[str, list[dict[str, Any]]] = {
        mode: [] for mode in sorted(MODES)
    }
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

    summary_modes = {mode: _aggregate_mode_metrics(rows) for mode, rows in by_mode_retrieval.items()}
    summary_slices = {
        slice_name: {
            mode: _aggregate_mode_metrics(rows)
            for mode, rows in per_mode.items()
        }
        for slice_name, per_mode in by_slice_mode_retrieval.items()
    }

    summary_semantic_focus = {
        mode: _aggregate_mode_metrics(rows)
        for mode, rows in semantic_focus_by_mode_retrieval.items()
    }
    summary_projection = {
        mode: _aggregate_projection_metrics(rows) for mode, rows in by_mode.items()
    }
    skew_alarms = {
        mode: _mode_skew_alarm(rows) for mode, rows in by_mode.items()
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
        "suite_id": "rust_reference_table1_retrieval_eval_v1",
        "checked_at": _utc_now(),
        "inputs": {
            "db_path": str(db_path),
            "contract_path": str(contract_path),
            "top_k": int(top_k),
            "candidate_limit": int(candidate_limit),
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
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--candidate-limit", type=int, default=DEFAULT_CANDIDATE_LIMIT)
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
    parser.add_argument("--semantic-retries", type=int, default=2)
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
        "--local-backend-engine",
        default=os.environ.get("RUST_REF_LOCAL_BACKEND_ENGINE", "python"),
    )
    parser.add_argument(
        "--local-backend-image",
        default="ghcr.io/huggingface/text-embeddings-inference:cpu-latest",
    )
    parser.add_argument("--local-embed-container", default="rust-ref-tei-embed")
    parser.add_argument("--local-rerank-container", default="rust-ref-tei-rerank")
    parser.add_argument(
        "--local-model-cache-dir",
        default=os.environ.get(
            "RUST_REF_SEMANTIC_MODEL_CACHE_DIR",
            os.environ.get("RUST_REF_TEI_MODEL_CACHE_DIR", ".cache/sqlite_kb/models/hf"),
        ),
    )
    parser.add_argument("--local-startup-timeout-sec", type=float, default=180.0)
    parser.add_argument(
        "--semantic-backend-profile",
        default=os.environ.get("RUST_REF_SEMANTIC_BACKEND_PROFILE", "python-local"),
        help="Semantic backend profile label written to reports",
    )
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
                "--engine",
                str(args.local_backend_engine),
                "--image",
                str(args.local_backend_image),
                "--embed-base-url",
                resolve_embed_base_url(semantic_config),
                "--rerank-base-url",
                resolve_rerank_base_url(semantic_config),
                "--embed-model-id",
                str(args.embed_model_id),
                "--rerank-model-id",
                str(args.reranker_model_id),
                "--embed-container",
                str(args.local_embed_container),
                "--rerank-container",
                str(args.local_rerank_container),
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
            backend_profile=str(args.semantic_backend_profile),
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
                    "--engine",
                    str(args.local_backend_engine),
                    "--embed-container",
                    str(args.local_embed_container),
                    "--rerank-container",
                    str(args.local_rerank_container),
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
