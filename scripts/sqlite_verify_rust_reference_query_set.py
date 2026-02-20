#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from sqlite_query_guardrails import GuardrailError, execute_contract_query

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3
EXPECTED_ROW_MARKERS = tuple(f"1{chr(ord('a') + idx)}" for idx in range(9))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"YAML payload at {path} is not a mapping")
    return payload


def load_query_suite(path: Path) -> list[dict[str, Any]]:
    payload = _load_yaml(path)
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Query suite must define non-empty 'rows' list")

    flattened: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Each row entry in query suite must be a mapping")

        row_marker = str(row.get("row_marker", "")).strip().lower()
        if row_marker not in EXPECTED_ROW_MARKERS:
            raise RuntimeError(f"Unexpected row_marker in query suite: {row_marker}")

        cases = row.get("cases")
        if not isinstance(cases, list) or len(cases) != 5:
            raise RuntimeError(f"Row {row_marker} must define exactly 5 cases")

        for case in cases:
            if not isinstance(case, dict):
                raise RuntimeError(f"Row {row_marker} contains non-mapping case entry")
            case_id = str(case.get("case_id", "")).strip()
            query_id = str(case.get("query_id", "")).strip()
            query_type = str(case.get("query_type", "")).strip()
            if not case_id or not query_id or not query_type:
                raise RuntimeError(f"Case in row {row_marker} missing case_id/query_id/query_type")
            if case_id in seen_case_ids:
                raise RuntimeError(f"Duplicate case_id in query suite: {case_id}")
            seen_case_ids.add(case_id)

            flattened.append(
                {
                    "row_marker": row_marker,
                    "case_id": case_id,
                    "query_id": query_id,
                    "query_type": query_type,
                    "params": case.get("params") or {},
                    "purpose": str(case.get("purpose", "")).strip(),
                }
            )

    markers = {case["row_marker"] for case in flattened}
    if markers != set(EXPECTED_ROW_MARKERS):
        raise RuntimeError("Query suite must include exactly row markers 1a..1i")

    if len(flattened) != 45:
        raise RuntimeError(f"Query suite must contain 45 cases, got {len(flattened)}")

    return flattened


def load_expected_cases(path: Path) -> dict[str, dict[str, Any]]:
    payload = _load_yaml(path)
    cases = payload.get("cases")
    if not isinstance(cases, dict) or not cases:
        raise RuntimeError("Expected-results file must define non-empty 'cases' mapping")

    normalized: dict[str, dict[str, Any]] = {}
    for case_id, case_expectation in cases.items():
        if not isinstance(case_id, str) or not case_id.strip():
            raise RuntimeError("Expected-results contains invalid case_id")
        if not isinstance(case_expectation, dict):
            raise RuntimeError(f"Expected-results case {case_id} must be a mapping")
        normalized[case_id] = case_expectation
    return normalized


def validate_suite_shapes(
    query_cases: list[dict[str, Any]],
    expected_cases: dict[str, dict[str, Any]],
    expected_case_count: int,
) -> None:
    query_case_ids = {case["case_id"] for case in query_cases}
    expected_case_ids = set(expected_cases.keys())
    missing_expected = sorted(query_case_ids - expected_case_ids)
    extra_expected = sorted(expected_case_ids - query_case_ids)

    if missing_expected:
        raise RuntimeError(
            f"Missing expected definitions for case ids: {', '.join(missing_expected)}"
        )
    if extra_expected:
        raise RuntimeError(
            f"Expected-results contains unknown case ids: {', '.join(extra_expected)}"
        )
    if len(query_case_ids) != expected_case_count:
        raise RuntimeError(f"Expected {expected_case_count} query cases, got {len(query_case_ids)}")


def _check_descending_relevance(rows: list[dict[str, Any]]) -> bool:
    if len(rows) < 2:
        return True
    prior = float(rows[0]["relevance_score"])
    for row in rows[1:]:
        current = float(row["relevance_score"])
        if current > prior:
            return False
        prior = current
    return True


def verify_query_suite(
    db_path: Path,
    contract_path: Path,
    query_cases: list[dict[str, Any]],
    expected_cases: dict[str, dict[str, Any]],
    query_log_root: Path,
) -> dict[str, Any]:
    row_verdict_result = execute_contract_query(
        db_path=db_path,
        contract_path=contract_path,
        query_id="row_verdicts_for_table1",
        params={},
        query_log_root=query_log_root,
    )
    row_lookup = {str(row["row_marker"]).lower(): row for row in row_verdict_result["rows"]}
    if set(row_lookup.keys()) != set(EXPECTED_ROW_MARKERS):
        raise RuntimeError("row_verdicts_for_table1 did not return full 1a..1i marker set")

    mechanism_cache: dict[str, list[dict[str, Any]]] = {}
    evidence_cache: dict[str, list[dict[str, Any]]] = {}

    case_results: list[dict[str, Any]] = []
    failures = 0
    passes = 0

    for case in query_cases:
        case_id = case["case_id"]
        row_marker = case["row_marker"]
        query_type = case["query_type"]
        query_id = case["query_id"]
        expected = expected_cases[case_id]
        row_record = row_lookup[row_marker]
        row_node_id = str(row_record["row_node_id"])

        status = "pass"
        reason = ""
        actual: dict[str, Any] = {}

        try:
            if query_type == "row_verdict":
                expected_verdict = str(expected.get("expected_verdict", ""))
                actual_verdict = str(row_record.get("verdict", ""))
                actual = {"actual_verdict": actual_verdict}
                if actual_verdict != expected_verdict:
                    status = "fail"
                    reason = f"Expected verdict {expected_verdict}, got {actual_verdict}"

            elif query_type == "rationale_anchor_timestamp":
                anchor_prefix = str(expected.get("anchor_prefix", ""))
                source_anchor = str(row_record.get("source_anchor", ""))
                rationale_timestamp = str(row_record.get("rationale_timestamp", "")).strip()
                actual = {
                    "source_anchor": source_anchor,
                    "rationale_timestamp": rationale_timestamp,
                }
                if anchor_prefix and not source_anchor.startswith(anchor_prefix):
                    status = "fail"
                    reason = f"source_anchor does not start with {anchor_prefix}"
                elif bool(expected.get("require_timestamp", False)) and not rationale_timestamp:
                    status = "fail"
                    reason = "Missing rationale_timestamp"

            elif query_type == "mechanisms_for_row":
                mechanisms = mechanism_cache.get(row_node_id)
                if mechanisms is None:
                    mechanisms = execute_contract_query(
                        db_path=db_path,
                        contract_path=contract_path,
                        query_id=query_id,
                        params={"row_node_id": row_node_id},
                        query_log_root=query_log_root,
                    )["rows"]
                    mechanism_cache[row_node_id] = mechanisms

                min_rows = int(expected.get("min_rows", 1))
                actual = {
                    "row_count": len(mechanisms),
                    "top_family": mechanisms[0]["mechanism_family"] if mechanisms else "",
                    "top_mechanism": mechanisms[0]["mechanism_id"] if mechanisms else "",
                }
                if len(mechanisms) < min_rows:
                    status = "fail"
                    reason = f"Expected >= {min_rows} mechanisms, got {len(mechanisms)}"
                elif bool(
                    expected.get("require_descending_relevance", False)
                ) and not _check_descending_relevance(mechanisms):
                    status = "fail"
                    reason = "Mechanism list is not sorted by descending relevance_score"
                elif bool(expected.get("require_source_timestamp", False)) and any(
                    not str(row.get("source_fetched_at", "")).strip() for row in mechanisms
                ):
                    status = "fail"
                    reason = "Mechanisms list contains missing source_fetched_at"
                else:
                    allowed = expected.get("allowed_top_families") or []
                    if allowed and mechanisms:
                        top_family = str(mechanisms[0]["mechanism_family"])
                        if top_family not in allowed:
                            status = "fail"
                            reason = (
                                f"Top mechanism family {top_family} "
                                f"not in allowed set {sorted(allowed)}"
                            )

            elif query_type == "lexical_statement_search":
                raw_params = case.get("params")
                if not isinstance(raw_params, dict):
                    raise RuntimeError("lexical_statement_search case params must be mapping")
                lexical_params = {
                    "row_node_id": row_node_id,
                    "term_a": str(raw_params.get("term_a", "")).strip(),
                    "term_b": str(raw_params.get("term_b", "")).strip(),
                    "term_c": str(raw_params.get("term_c", "")).strip(),
                }
                if not all(lexical_params[key] for key in ("term_a", "term_b", "term_c")):
                    raise RuntimeError("lexical_statement_search requires term_a/term_b/term_c")

                lexical_rows = execute_contract_query(
                    db_path=db_path,
                    contract_path=contract_path,
                    query_id=query_id,
                    params=lexical_params,
                    query_log_root=query_log_root,
                )["rows"]

                min_rows = int(expected.get("min_rows", 1))
                min_top_score = int(expected.get("min_top_lexical_score", 1))
                top_score = int(lexical_rows[0]["lexical_score"]) if lexical_rows else 0
                actual = {
                    "row_count": len(lexical_rows),
                    "top_lexical_score": top_score,
                }

                if len(lexical_rows) < min_rows:
                    status = "fail"
                    reason = f"Expected >= {min_rows} lexical hits, got {len(lexical_rows)}"
                elif top_score < min_top_score:
                    status = "fail"
                    reason = f"Top lexical score {top_score} < required {min_top_score}"
                elif bool(expected.get("require_source_timestamp", False)) and any(
                    not str(row.get("source_fetched_at", "")).strip() for row in lexical_rows
                ):
                    status = "fail"
                    reason = "Lexical hits contain missing source_fetched_at"

            elif query_type == "top_mechanism_evidence":
                mechanisms = mechanism_cache.get(row_node_id)
                if mechanisms is None:
                    mechanisms = execute_contract_query(
                        db_path=db_path,
                        contract_path=contract_path,
                        query_id="mechanisms_for_row",
                        params={"row_node_id": row_node_id},
                        query_log_root=query_log_root,
                    )["rows"]
                    mechanism_cache[row_node_id] = mechanisms

                if not mechanisms:
                    status = "fail"
                    reason = "No mechanisms available to resolve top mechanism evidence"
                    actual = {"row_count": 0}
                else:
                    mechanism_id = str(mechanisms[0]["mechanism_id"])
                    evidence_rows = evidence_cache.get(mechanism_id)
                    if evidence_rows is None:
                        evidence_rows = execute_contract_query(
                            db_path=db_path,
                            contract_path=contract_path,
                            query_id=query_id,
                            params={"mechanism_id": mechanism_id},
                            query_log_root=query_log_root,
                        )["rows"]
                        evidence_cache[mechanism_id] = evidence_rows

                    min_rows = int(expected.get("min_rows", 1))
                    min_confidence = float(expected.get("min_confidence", 0.0))
                    anchor_prefix = str(expected.get("anchor_prefix", ""))
                    actual = {
                        "top_mechanism": mechanism_id,
                        "row_count": len(evidence_rows),
                        "min_confidence": min(
                            (float(row.get("confidence", 0.0)) for row in evidence_rows),
                            default=0.0,
                        ),
                    }

                    if len(evidence_rows) < min_rows:
                        status = "fail"
                        reason = f"Expected >= {min_rows} evidence rows, got {len(evidence_rows)}"
                    elif any(
                        float(row.get("confidence", 0.0)) < min_confidence for row in evidence_rows
                    ):
                        status = "fail"
                        reason = f"Evidence contains confidence below {min_confidence}"
                    elif anchor_prefix and any(
                        not str(row.get("source_anchor", "")).startswith(anchor_prefix)
                        for row in evidence_rows
                    ):
                        status = "fail"
                        reason = f"Evidence source_anchor missing prefix {anchor_prefix}"
                    elif bool(expected.get("require_source_timestamp", False)) and any(
                        not str(row.get("source_fetched_at", "")).strip() for row in evidence_rows
                    ):
                        status = "fail"
                        reason = "Evidence rows contain missing source_fetched_at"

            else:
                status = "fail"
                reason = f"Unknown query_type {query_type}"

        except Exception as exc:  # pragma: no cover - runtime safety
            status = "fail"
            reason = f"Runtime error while evaluating case: {exc}"

        if status == "pass":
            passes += 1
        else:
            failures += 1

        case_results.append(
            {
                "case_id": case_id,
                "row_marker": row_marker,
                "query_type": query_type,
                "query_id": query_id,
                "status": status,
                "reason": reason,
                "actual": actual,
                "expected": expected,
            }
        )

    report = {
        "suite_id": "rust_reference_table1_reasonableness_v1",
        "checked_at": _utc_now(),
        "summary": {
            "total_cases": len(case_results),
            "passed_cases": passes,
            "failed_cases": failures,
            "row_markers": list(EXPECTED_ROW_MARKERS),
        },
        "cases": case_results,
    }

    if failures > 0:
        report["remediation_required"] = True
        report["remediation_message"] = (
            "One or more reasonableness cases failed. Stop progression and remediate "
            "query/searchability before proceeding."
        )
    else:
        report["remediation_required"] = False

    return report


def _default_report_path(root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return root / ".cache/sqlite_kb/reports/rust_reference" / f"query_set_verification_{stamp}.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify 45-case Rust Reference Table 1 query reasonableness suite"
    )
    parser.add_argument(
        "--db-path",
        default=".cache/sqlite_kb/current/rust_reference.sqlite",
        help="Path to rust_reference sqlite database",
    )
    parser.add_argument(
        "--contract-path",
        default="config/sqlite_query_contracts/rust_reference.yaml",
        help="Path to query contract file",
    )
    parser.add_argument(
        "--queries-path",
        default="data/query_testsets/rust_reference_table1_queries.yaml",
        help="Path to query suite definitions",
    )
    parser.add_argument(
        "--expected-path",
        default="data/query_testsets/rust_reference_table1_expected.yaml",
        help="Path to expected-result definitions",
    )
    parser.add_argument(
        "--query-log-root",
        default=".cache/sqlite_kb/query_logs/rust_reference",
        help="Directory for query audit logs",
    )
    parser.add_argument(
        "--report-path",
        default=None,
        help="Optional output path for verification report",
    )
    parser.add_argument(
        "--expected-case-count",
        type=int,
        default=45,
        help="Expected number of verification cases",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]

    db_path = (root / args.db_path).resolve()
    contract_path = (root / args.contract_path).resolve()
    queries_path = (root / args.queries_path).resolve()
    expected_path = (root / args.expected_path).resolve()
    query_log_root = (root / args.query_log_root).resolve()
    report_path = (
        (root / args.report_path).resolve() if args.report_path else _default_report_path(root)
    )

    try:
        query_cases = load_query_suite(queries_path)
        expected_cases = load_expected_cases(expected_path)
        validate_suite_shapes(
            query_cases=query_cases,
            expected_cases=expected_cases,
            expected_case_count=args.expected_case_count,
        )
        report = verify_query_suite(
            db_path=db_path,
            contract_path=contract_path,
            query_cases=query_cases,
            expected_cases=expected_cases,
            query_log_root=query_log_root,
        )
    except (RuntimeError, GuardrailError, OSError) as exc:
        print(f"[verify-rust-reference-query-set][error] {exc}")
        return EXIT_RUNTIME_FAIL

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"[verify-rust-reference-query-set] report -> {report_path}")

    if report["summary"]["failed_cases"] > 0:
        print("[verify-rust-reference-query-set][error] Reasonableness failures detected")
        return EXIT_RUNTIME_FAIL
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
