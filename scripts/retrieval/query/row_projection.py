from __future__ import annotations

from typing import Any, Protocol


class RowProjectionPolicyLike(Protocol):
    @property
    def thresholds(self) -> dict[str, float]: ...

    @property
    def top_score_floor(self) -> float: ...

    @property
    def min_evidence_hits(self) -> int: ...

    @property
    def margin(self) -> float: ...


def build_row_projection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    row_scores: dict[str, float] = {}
    row_evidence_count: dict[str, int] = {}
    row_evidence_trace: dict[str, list[dict[str, Any]]] = {}

    for rank, row in enumerate(rows, start=1):
        relevance = float(
            row.get(
                "relevance_score",
                row.get("lexical_score", 0.0),
            )
        )
        rank_weight = 1.0 / float(rank)
        marker_scores = row.get("row_marker_scores", [])
        if not isinstance(marker_scores, list):
            marker_scores = []

        for marker_score in marker_scores:
            if not isinstance(marker_score, dict):
                continue
            key = str(marker_score.get("row_marker", "")).strip().lower()
            if not key:
                continue
            profile_score = float(marker_score.get("score", 0.0))
            if profile_score <= 0.0:
                continue
            contribution = relevance * rank_weight * profile_score
            row_scores[key] = row_scores.get(key, 0.0) + contribution
            row_evidence_count[key] = row_evidence_count.get(key, 0) + 1
            row_evidence_trace.setdefault(key, []).append(
                {
                    "statement_id": str(row.get("statement_id", "")),
                    "source_anchor": str(row.get("source_anchor", "")),
                    "contribution": float(contribution),
                }
            )

    projection: list[dict[str, Any]] = []
    for marker, score in row_scores.items():
        trace = row_evidence_trace.get(marker, [])
        trace.sort(
            key=lambda row: (
                -float(row.get("contribution", 0.0)),
                str(row.get("statement_id", "")),
            )
        )
        rounded_trace = [
            {
                "statement_id": str(item.get("statement_id", "")),
                "source_anchor": str(item.get("source_anchor", "")),
                "contribution": round(float(item.get("contribution", 0.0)), 6),
            }
            for item in trace[:10]
        ]

        top = rounded_trace[0] if rounded_trace else {}
        projection.append(
            {
                "row_marker": marker,
                "score": round(score, 6),
                "evidence_hits": int(row_evidence_count.get(marker, 0)),
                "top_statement_id": str(top.get("statement_id", "")),
                "top_source_anchor": str(top.get("source_anchor", "")),
                "evidence_trace": rounded_trace,
            }
        )

    projection.sort(key=lambda row: (-float(row["score"]), str(row["row_marker"])))
    return projection


def apply_abstain_policy(
    projection: list[dict[str, Any]],
    *,
    policy: RowProjectionPolicyLike,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    thresholds = dict(policy.thresholds)
    top_score_floor = float(policy.top_score_floor)
    min_evidence_hits = max(1, int(policy.min_evidence_hits))
    margin_threshold = max(0.0, float(policy.margin))
    if not projection:
        return [], {
            "active": True,
            "reason_code": "NO_ROW_SIGNAL",
            "detail": "No row marker evidence was generated from retrieved chunks",
            "thresholds": thresholds,
        }

    top = projection[0]
    top_marker = str(top.get("row_marker", "")).strip().lower()
    top_score = float(top.get("score", 0.0))
    top_hits = int(top.get("evidence_hits", 0))
    threshold = float(thresholds.get(top_marker, 0.015))
    effective_threshold = max(float(threshold), float(top_score_floor))

    if top_hits < min_evidence_hits:
        return [], {
            "active": True,
            "reason_code": "INSUFFICIENT_EVIDENCE",
            "detail": f"Top row {top_marker} has evidence_hits={top_hits}, required={min_evidence_hits}",
            "thresholds": thresholds,
        }

    if top_score < effective_threshold:
        return [], {
            "active": True,
            "reason_code": "ROW_SCORE_BELOW_THRESHOLD",
            "detail": f"Top row {top_marker} score={top_score:.6f} threshold={effective_threshold:.6f}",
            "thresholds": thresholds,
        }

    if len(projection) > 1:
        second_score = float(projection[1].get("score", 0.0))
        margin = top_score - second_score
        if margin < margin_threshold:
            return [], {
                "active": True,
                "reason_code": "LOW_CONFIDENCE_MARGIN",
                "detail": (f"Top-vs-second margin={margin:.6f} required>={margin_threshold:.6f}"),
                "thresholds": thresholds,
            }

    selected: list[dict[str, Any]] = []
    for row in projection:
        marker = str(row.get("row_marker", "")).strip().lower()
        score = float(row.get("score", 0.0))
        hits = int(row.get("evidence_hits", 0))
        min_score = float(thresholds.get(marker, threshold))
        if hits < min_evidence_hits:
            continue
        if score < min_score:
            continue
        selected.append(row)
        if len(selected) >= 3:
            break

    if not selected:
        return [], {
            "active": True,
            "reason_code": "NO_ROW_ABOVE_THRESHOLD",
            "detail": "No row markers satisfied score and evidence thresholds",
            "thresholds": thresholds,
        }

    return selected, {
        "active": False,
        "reason_code": "NONE",
        "detail": "Row projection produced calibrated labels",
        "thresholds": thresholds,
    }
