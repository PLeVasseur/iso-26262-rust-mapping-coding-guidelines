from __future__ import annotations

import math
from typing import Any

ROW_MARKERS = tuple(f"1{chr(ord('a') + idx)}" for idx in range(9))


def precision_at_k(relevance: list[int], k: int) -> float:
    if k <= 0:
        return 0.0
    observed = relevance[:k]
    if not observed:
        return 0.0
    return float(sum(observed)) / float(len(observed))


def mrr_at_k(relevance: list[int], k: int) -> float:
    for rank, value in enumerate(relevance[:k], start=1):
        if value:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(relevance: list[int], k: int) -> float:
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


def row_hit(rows: list[dict[str, Any]], expected_row_markers: list[str], k: int) -> float:
    if not expected_row_markers:
        return 1.0
    expected = set(expected_row_markers)
    if not expected:
        return 1.0
    for row in rows[:k]:
        markers = {value.lower() for value in row.get("row_markers", [])}
        if expected.intersection(markers):
            return 1.0
    return 0.0


def anchor_hit(rows: list[dict[str, Any]], anchor_prefixes: list[str], k: int) -> float:
    prefixes = [prefix for prefix in anchor_prefixes if prefix]
    if not prefixes:
        return 1.0
    for row in rows[:k]:
        source_anchor = str(row.get("source_anchor", ""))
        if any(source_anchor.startswith(prefix) for prefix in prefixes):
            return 1.0
    return 0.0


def projection_f1(expected: set[str], predicted: set[str]) -> tuple[float, float, float]:
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


def aggregate_mode_metrics(case_rows: list[dict[str, Any]]) -> dict[str, float]:
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


def aggregate_projection_metrics(case_rows: list[dict[str, Any]]) -> dict[str, float]:
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


def aggregate_duration_metrics(case_rows: list[dict[str, Any]]) -> dict[str, float]:
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


def mode_skew_alarm(
    case_rows: list[dict[str, Any]],
    *,
    max_row_share_threshold: float = 0.75,
    min_row_entropy_threshold: float = 0.35,
    min_abstain_rate_with_expected_threshold: float = 0.20,
) -> dict[str, Any]:
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

    alarms: list[str] = []
    if max_row_share > float(max_row_share_threshold):
        alarms.append(f"max_row_share>{max_row_share_threshold}: {max_row_share:.4f}")
    if total > 1 and row_entropy < float(min_row_entropy_threshold):
        alarms.append(f"row_entropy<{min_row_entropy_threshold}: {row_entropy:.4f}")
    if expected_abstain_count > 0 and abstain_rate < float(
        min_abstain_rate_with_expected_threshold
    ):
        alarms.append(
            "abstain_rate_collapse: "
            f"{abstain_rate:.4f} < {min_abstain_rate_with_expected_threshold}"
        )

    return {
        "max_row_share": round(float(max_row_share), 6),
        "row_entropy": round(float(row_entropy), 6),
        "row_gini": round(float(row_gini), 6),
        "abstain_rate": round(float(abstain_rate), 6),
        "expected_abstain_count": int(expected_abstain_count),
        "predicted_distribution": distribution,
        "alerts": alarms,
    }
