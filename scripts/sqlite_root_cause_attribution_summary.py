#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object at {path}")
    return payload


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _round(value: float) -> float:
    return round(float(value), 6)


def _safe_pct(numerator: float, denominator: float) -> float:
    if denominator <= 0.0:
        return 0.0
    return _round((float(numerator) / float(denominator)) * 100.0)


def _classify_fix_lane(
    *,
    python_pct: float,
    model_pct: float,
    timeout_pct: float,
    lock_pct: float,
) -> str:
    if python_pct > 35.0:
        return "python_overhead_dominant"
    if model_pct > 60.0:
        return "model_compute_dominant"
    if timeout_pct > 15.0:
        return "timeout_policy_dominant"
    if lock_pct > 10.0:
        return "lock_contention_dominant"
    return "mixed_or_unclear"


def _tool_status() -> dict[str, bool]:
    py_spy_available = bool(shutil.which("py-spy"))
    if not py_spy_available:
        try:
            completed = subprocess.run(
                ["uvx", "--from", "py-spy==0.4.1", "py-spy", "--version"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            py_spy_available = completed.returncode == 0
        except OSError:
            py_spy_available = False
    return {
        "py_spy_available": py_spy_available,
        "perf_available": bool(shutil.which("perf")),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate reproducible root-cause attribution summary"
    )
    parser.add_argument("--eval-path", required=True, help="Path to eval.json")
    parser.add_argument(
        "--backend-attempts-path",
        required=True,
        help="Path to backend_attempts.jsonl",
    )
    parser.add_argument(
        "--worker-spans-path",
        required=True,
        help="Path to worker_rerank_requests.jsonl",
    )
    parser.add_argument("--output-path", required=True, help="Path to write summary.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]

    eval_path = (root / str(args.eval_path)).resolve()
    backend_attempts_path = (root / str(args.backend_attempts_path)).resolve()
    worker_spans_path = (root / str(args.worker_spans_path)).resolve()
    output_path = (root / str(args.output_path)).resolve()

    try:
        eval_payload = _load_json(eval_path)
        backend_attempt_rows = _load_jsonl(backend_attempts_path)
        worker_span_rows = _load_jsonl(worker_spans_path)
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"[root-cause-summary][error] {exc}")
        return EXIT_RUNTIME_FAIL

    cases = [row for row in list(eval_payload.get("cases", [])) if isinstance(row, dict)]

    total_case_ms = sum(_to_float(row.get("duration_ms", 0.0)) for row in cases)
    timing_bucket_ms = sum(
        _to_float(row.get("preflight_ms", 0.0))
        + _to_float(row.get("lexical_ms", 0.0))
        + _to_float(row.get("semantic_embed_ms", 0.0))
        + _to_float(row.get("semantic_score_ms", 0.0))
        + _to_float(row.get("rerank_ms", 0.0))
        + _to_float(row.get("projection_ms", 0.0))
        for row in cases
    )

    backend_attempt_total_ms = sum(
        _to_float(row.get("duration_ms", 0.0)) for row in backend_attempt_rows
    )
    backend_timeout_ms = sum(
        _to_float(row.get("duration_ms", 0.0))
        for row in backend_attempt_rows
        if str(row.get("error_class", "")) == "timeout"
    )

    worker_total_ms = sum(_to_float(row.get("total_ms", 0.0)) for row in worker_span_rows)
    worker_queue_wait_ms = sum(_to_float(row.get("queue_wait_ms", 0.0)) for row in worker_span_rows)
    worker_lock_wait_ms = sum(_to_float(row.get("lock_wait_ms", 0.0)) for row in worker_span_rows)
    worker_tokenize_ms = sum(_to_float(row.get("tokenize_ms", 0.0)) for row in worker_span_rows)
    worker_model_forward_ms = sum(
        _to_float(row.get("model_forward_ms", 0.0)) for row in worker_span_rows
    )
    worker_postprocess_ms = sum(
        _to_float(row.get("postprocess_ms", 0.0)) for row in worker_span_rows
    )
    worker_serialize_ms = sum(_to_float(row.get("serialize_ms", 0.0)) for row in worker_span_rows)

    python_overhead_ms = (
        worker_queue_wait_ms
        + worker_lock_wait_ms
        + worker_tokenize_ms
        + worker_postprocess_ms
        + worker_serialize_ms
    )
    model_forward_ms = worker_model_forward_ms
    network_or_io_ms = max(0.0, worker_total_ms - (python_overhead_ms + model_forward_ms))

    explained_ms = min(
        total_case_ms,
        max(timing_bucket_ms, backend_attempt_total_ms, worker_total_ms),
    )

    python_overhead_pct = _safe_pct(python_overhead_ms, total_case_ms)
    model_forward_pct = _safe_pct(model_forward_ms, total_case_ms)
    timeout_waste_pct = _safe_pct(backend_timeout_ms, total_case_ms)
    lock_wait_pct = _safe_pct(worker_lock_wait_ms, total_case_ms)

    attempt_trace_ids = {
        str(row.get("trace_id", "")).strip()
        for row in backend_attempt_rows
        if str(row.get("trace_id", "")).strip()
    }
    worker_trace_ids = {
        str(row.get("trace_id", "")).strip()
        for row in worker_span_rows
        if str(row.get("trace_id", "")).strip()
    }
    joined_trace_ids = attempt_trace_ids.intersection(worker_trace_ids)

    correlated_attempt_rows = sum(
        1
        for row in backend_attempt_rows
        if str(row.get("trace_id", "")).strip() in worker_trace_ids
    )

    hotspot_components = [
        {"name": "worker_model_forward_ms", "total_ms": _round(model_forward_ms)},
        {"name": "worker_tokenize_ms", "total_ms": _round(worker_tokenize_ms)},
        {"name": "worker_lock_wait_ms", "total_ms": _round(worker_lock_wait_ms)},
        {"name": "worker_postprocess_ms", "total_ms": _round(worker_postprocess_ms)},
        {"name": "backend_timeout_waste_ms", "total_ms": _round(backend_timeout_ms)},
        {"name": "worker_network_or_io_ms", "total_ms": _round(network_or_io_ms)},
    ]
    hotspot_components.sort(key=lambda row: float(row["total_ms"]), reverse=True)

    summary = {
        "inputs": {
            "eval_path": str(eval_path),
            "backend_attempts_path": str(backend_attempts_path),
            "worker_spans_path": str(worker_spans_path),
        },
        "tooling": _tool_status(),
        "counts": {
            "case_count": int(len(cases)),
            "backend_attempt_rows": int(len(backend_attempt_rows)),
            "worker_span_rows": int(len(worker_span_rows)),
        },
        "timing_totals_ms": {
            "total_case_ms": _round(total_case_ms),
            "timing_bucket_ms": _round(timing_bucket_ms),
            "backend_attempt_total_ms": _round(backend_attempt_total_ms),
            "worker_total_ms": _round(worker_total_ms),
            "explained_ms": _round(explained_ms),
        },
        "attribution": {
            "python_runtime_overhead_ms": _round(python_overhead_ms),
            "model_forward_compute_ms": _round(model_forward_ms),
            "network_or_io_overhead_ms": _round(network_or_io_ms),
            "timeout_waste_ms": _round(backend_timeout_ms),
            "python_runtime_overhead_pct": python_overhead_pct,
            "model_forward_compute_pct": model_forward_pct,
            "timeout_waste_pct": timeout_waste_pct,
            "lock_wait_pct": lock_wait_pct,
            "explained_pct": _safe_pct(explained_ms, total_case_ms),
        },
        "correlation": {
            "attempt_trace_id_count": int(len(attempt_trace_ids)),
            "worker_trace_id_count": int(len(worker_trace_ids)),
            "joined_trace_id_count": int(len(joined_trace_ids)),
            "trace_id_join_rate_pct": _safe_pct(len(joined_trace_ids), len(attempt_trace_ids)),
            "attempt_row_join_rate_pct": _safe_pct(
                correlated_attempt_rows,
                len(backend_attempt_rows),
            ),
        },
        "hotspots": hotspot_components[:3],
        "recommended_fix_lane": _classify_fix_lane(
            python_pct=python_overhead_pct,
            model_pct=model_forward_pct,
            timeout_pct=timeout_waste_pct,
            lock_pct=lock_wait_pct,
        ),
    }

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[root-cause-summary][error] {exc}")
        return EXIT_RUNTIME_FAIL

    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"[root-cause-summary] report -> {output_path}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
