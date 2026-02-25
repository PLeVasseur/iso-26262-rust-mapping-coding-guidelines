from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from retrieval.eval.human_report_resolvers.base import ChunkRecord, HumanReportResolver


@dataclass(frozen=True)
class HumanReportConfig:
    eval_path: Path
    db_path: Path
    output_path: Path
    testset_path: Path | None
    top_n: int
    snippet_chars: int
    only_problem_prompts: bool


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object at {path}")
    return payload


def _load_expectations(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    prompts = payload.get("prompts", []) if isinstance(payload, dict) else []
    if not isinstance(prompts, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in prompts:
        if not isinstance(row, dict):
            continue
        prompt_id = str(row.get("prompt_id", "")).strip()
        if not prompt_id:
            continue
        out[prompt_id] = {
            "query_text": str(row.get("query_text", "")).strip(),
            "expected_row_markers": [
                str(marker).strip()
                for marker in list(row.get("expected_row_markers", []))
                if marker
            ],
            "expect_abstain": bool(row.get("expect_abstain", False)),
            "slice": str(row.get("slice", "")).strip(),
        }
    return out


def _normalize_chunk_ids(raw_ids: list[Any], top_n: int) -> list[str]:
    out: list[str] = []
    for value in raw_ids[:top_n]:
        chunk_id = str(value).strip()
        if chunk_id:
            out.append(chunk_id)
    return out


def _candidate_chunk_ids(chunk_id: str) -> list[str]:
    if chunk_id.startswith("chunk::"):
        return [chunk_id, chunk_id[7:]]
    return [chunk_id, f"chunk::{chunk_id}"]


def _escape(text: str) -> str:
    return " ".join(str(text).replace("|", "\\|").split())


def _is_problem_prompt(cases: list[dict[str, Any]]) -> bool:
    for case in cases:
        if str(case.get("status", "")).strip().lower() != "pass":
            return True
        if bool(case.get("expect_abstain", False)) and not bool(case.get("abstain_active", False)):
            return True
    return False


def _sort_prompt_ids(cases: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for case in cases:
        prompt_id = str(case.get("prompt_id", "")).strip()
        if not prompt_id or prompt_id in seen:
            continue
        seen.add(prompt_id)
        ordered.append(prompt_id)
    return ordered


def _collect_unique_chunk_ids(
    cases_by_prompt: dict[str, list[dict[str, Any]]],
    prompt_ids: list[str],
    top_n: int,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for prompt_id in prompt_ids:
        for case in cases_by_prompt.get(prompt_id, []):
            ids = _normalize_chunk_ids(list(case.get("top_statement_ids", [])), top_n)
            for chunk_id in ids:
                for candidate in _candidate_chunk_ids(chunk_id):
                    if candidate not in seen:
                        seen.add(candidate)
                        out.append(candidate)
    return out


def _render_mode_table(cases: list[dict[str, Any]]) -> list[str]:
    by_mode: dict[str, dict[str, Any]] = {}
    for case in cases:
        mode = str(case.get("mode", "")).strip()
        if mode:
            by_mode[mode] = case
    lines = [
        "| mode | status | mrr@k | precision@k | ndcg@k | row_hit_rate | abstain_active |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for mode in ("lexical", "semantic", "hybrid"):
        case = by_mode.get(mode, {})
        lines.append(
            "| "
            + " | ".join(
                [
                    mode,
                    str(case.get("status", "")),
                    f"{float(case.get('mrr_at_k', 0.0)):.6f}" if case else "",
                    f"{float(case.get('precision_at_k', 0.0)):.6f}" if case else "",
                    f"{float(case.get('ndcg_at_k', 0.0)):.6f}" if case else "",
                    f"{float(case.get('row_hit_rate', 0.0)):.6f}" if case else "",
                    "yes" if bool(case.get("abstain_active", False)) else "no" if case else "",
                ]
            )
            + " |"
        )
    return lines


def _render_chunk_table(
    *,
    case: dict[str, Any],
    resolver: HumanReportResolver,
    records: dict[str, ChunkRecord],
    top_n: int,
    missing_rows: list[tuple[str, str, int, str]],
) -> list[str]:
    prompt_id = str(case.get("prompt_id", "")).strip()
    mode = str(case.get("mode", "")).strip()
    chunk_ids = _normalize_chunk_ids(list(case.get("top_statement_ids", [])), top_n)

    header_cols = ["rank", "chunk_uid"]
    header_cols.extend(column.label for column in resolver.extra_columns)
    header_cols.extend(["section_heading", "source_anchor", "snippet"])
    lines = [
        "| " + " | ".join(header_cols) + " |",
        "| " + " | ".join(["---"] * len(header_cols)) + " |",
    ]

    for index, chunk_id in enumerate(chunk_ids, start=1):
        candidates = _candidate_chunk_ids(chunk_id)
        record = next(
            (records[candidate] for candidate in candidates if candidate in records), None
        )
        if record is None:
            missing_rows.append((prompt_id, mode, index, chunk_id))
            extras = ["" for _ in resolver.extra_columns]
            row = [str(index), _escape(chunk_id), *extras, "", "", ""]
            lines.append("| " + " | ".join(row) + " |")
            continue

        extra_values = [
            _escape(record.extras.get(column.key, "")) for column in resolver.extra_columns
        ]
        row = [
            str(index),
            _escape(chunk_id),
            *extra_values,
            _escape(record.section_heading),
            _escape(record.source_anchor),
            _escape(record.snippet),
        ]
        lines.append("| " + " | ".join(row) + " |")

    return lines


def build_markdown_report(
    *,
    eval_payload: dict[str, Any],
    expectations: dict[str, dict[str, Any]],
    resolver: HumanReportResolver,
    records: dict[str, ChunkRecord],
    config: HumanReportConfig,
) -> str:
    cases = [row for row in list(eval_payload.get("cases", [])) if isinstance(row, dict)]
    cases_by_prompt: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        prompt_id = str(case.get("prompt_id", "")).strip()
        if not prompt_id:
            continue
        cases_by_prompt.setdefault(prompt_id, []).append(case)

    prompt_ids = _sort_prompt_ids(cases)
    if config.only_problem_prompts:
        prompt_ids = [
            prompt_id
            for prompt_id in prompt_ids
            if _is_problem_prompt(cases_by_prompt.get(prompt_id, []))
        ]

    summary = eval_payload.get("summary", {}) if isinstance(eval_payload, dict) else {}
    gate_failures = list(summary.get("gate_failures", [])) if isinstance(summary, dict) else []

    lines: list[str] = []
    lines.append("# Retrieval Eval Human Review Report")
    lines.append("")
    lines.append(f"- generated_at: {datetime.now(UTC).isoformat(timespec='seconds')}")
    lines.append(f"- corpus: {resolver.corpus}")
    lines.append(f"- eval_path: {config.eval_path}")
    lines.append(f"- db_path: {config.db_path}")
    lines.append(f"- testset_path: {config.testset_path if config.testset_path else '(none)'}")
    lines.append(f"- prompts_in_report: {len(prompt_ids)}")
    lines.append("")

    lines.append("## Aggregate")
    lines.append("")
    lines.append(f"- total_mode_cases: {summary.get('total_mode_cases', 0)}")
    lines.append(f"- passed_cases: {summary.get('passed_cases', 0)}")
    lines.append(f"- failed_cases: {summary.get('failed_cases', 0)}")
    lines.append(f"- enforce_gates: {summary.get('enforce_gates', True)}")
    lines.append("")

    lines.append("## Gate Failures")
    lines.append("")
    if gate_failures:
        for failure in gate_failures:
            lines.append(f"- {failure}")
    else:
        lines.append("- (none)")
    lines.append("")

    missing_rows: list[tuple[str, str, int, str]] = []
    for prompt_id in prompt_ids:
        prompt_cases = cases_by_prompt.get(prompt_id, [])
        expectation = expectations.get(prompt_id, {})
        query_text = str(expectation.get("query_text") or prompt_cases[0].get("query_text", ""))
        expected_rows = list(expectation.get("expected_row_markers", []))
        expect_abstain = bool(
            expectation.get("expect_abstain", prompt_cases[0].get("expect_abstain", False))
        )
        slice_name = str(expectation.get("slice") or prompt_cases[0].get("slice", ""))

        lines.append(f"## {prompt_id}")
        lines.append("")
        lines.append(f"- query: {_escape(query_text)}")
        lines.append(f"- slice: {_escape(slice_name)}")
        lines.append(f"- expect_abstain: {str(expect_abstain).lower()}")
        lines.append(
            "- expected_row_markers: " + (", ".join(expected_rows) if expected_rows else "(none)")
        )
        lines.append("")

        lines.extend(_render_mode_table(prompt_cases))
        lines.append("")

        by_mode = {str(case.get("mode", "")).strip(): case for case in prompt_cases}
        for mode in ("lexical", "semantic", "hybrid"):
            case = by_mode.get(mode)
            if not case:
                continue
            lines.append(f"### {mode} top chunks")
            lines.append("")
            lines.extend(
                _render_chunk_table(
                    case=case,
                    resolver=resolver,
                    records=records,
                    top_n=config.top_n,
                    missing_rows=missing_rows,
                )
            )
            lines.append("")

    lines.append("## Missing Chunk Rows")
    lines.append("")
    if missing_rows:
        lines.append("| prompt_id | mode | rank | chunk_uid |")
        lines.append("| --- | --- | ---: | --- |")
        for prompt_id, mode, rank, chunk_uid in missing_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _escape(prompt_id),
                        _escape(mode),
                        str(rank),
                        _escape(chunk_uid),
                    ]
                )
                + " |"
            )
    else:
        lines.append("- (none)")
    lines.append("")
    return "\n".join(lines)


def generate_human_report(*, resolver: HumanReportResolver, config: HumanReportConfig) -> Path:
    eval_payload = _load_json(config.eval_path)
    expectations = _load_expectations(config.testset_path)

    cases = [row for row in list(eval_payload.get("cases", [])) if isinstance(row, dict)]
    cases_by_prompt: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        prompt_id = str(case.get("prompt_id", "")).strip()
        if not prompt_id:
            continue
        cases_by_prompt.setdefault(prompt_id, []).append(case)

    prompt_ids = _sort_prompt_ids(cases)
    if config.only_problem_prompts:
        prompt_ids = [
            prompt_id
            for prompt_id in prompt_ids
            if _is_problem_prompt(cases_by_prompt.get(prompt_id, []))
        ]

    chunk_ids = _collect_unique_chunk_ids(cases_by_prompt, prompt_ids, config.top_n)
    with sqlite3.connect(config.db_path) as conn:
        records = resolver.fetch_chunk_records(
            conn=conn,
            chunk_ids=chunk_ids,
            snippet_chars=config.snippet_chars,
        )

    markdown = build_markdown_report(
        eval_payload=eval_payload,
        expectations=expectations,
        resolver=resolver,
        records=records,
        config=config,
    )

    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    config.output_path.write_text(markdown, encoding="utf-8")
    return config.output_path
