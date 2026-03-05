from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, sort_keys=False) for row in rows)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def write_writer_outputs(
    *,
    writer_root: Path,
    role_rows: dict[str, list[dict[str, Any]]],
    contract_snapshot: dict[str, Any],
    invocation_trace: list[dict[str, Any]],
    merge_validation_report: dict[str, Any],
) -> None:
    write_json(writer_root / "prompt_contract_snapshot.json", contract_snapshot)
    write_json(writer_root / "subagent_invocation_trace.json", {"entries": invocation_trace})
    write_json(writer_root / "merge_validation_report.json", merge_validation_report)
    for role_name, rows in role_rows.items():
        write_jsonl(writer_root / f"{role_name}.jsonl", rows)


def write_normalization_report(path: Path, *, run_id: str, rows: list[dict[str, Any]]) -> None:
    canonical = 0
    detail_rows: list[dict[str, Any]] = []
    for row in rows:
        output = row.get("output") if isinstance(row, dict) else None
        if not isinstance(output, dict):
            continue
        prompt_id = str(output.get("prompt_id", ""))
        construct_scope = output.get("construct_scope")
        claim_map = output.get("claim_to_evidence_map")
        claim_id_ok = True
        if isinstance(claim_map, list):
            for index, claim in enumerate(claim_map, start=1):
                if not isinstance(claim, dict):
                    claim_id_ok = False
                    break
                expected = f"{prompt_id}::claim::{index}"
                if str(claim.get("claim_id", "")).strip() != expected:
                    claim_id_ok = False
                    break
        canonical_shape = (
            isinstance(construct_scope, list) and isinstance(claim_map, list) and claim_id_ok
        )
        if canonical_shape:
            canonical += 1
        detail_rows.append(
            {
                "target_id": str(output.get("target_id", "")),
                "prompt_id": prompt_id,
                "construct_scope_is_list": isinstance(construct_scope, list),
                "claim_map_is_list": isinstance(claim_map, list),
                "claim_id_format_ok": claim_id_ok,
                "canonical": canonical_shape,
            }
        )
    total = len(detail_rows)
    write_json(
        path,
        {
            "run_id": run_id,
            "status": "pass" if total and canonical == total else "fail",
            "canonical_count": canonical,
            "total_count": total,
            "canonical_rate": round(canonical / float(total), 4) if total else 0.0,
            "results": detail_rows,
        },
    )


def write_evidence_gate_report(
    path: Path,
    *,
    run_id: str,
    rows: list[dict[str, Any]],
    evidence_id_by_target: dict[str, set[str]],
) -> None:
    results: list[dict[str, Any]] = []
    status = "pass"
    for row in rows:
        output = row.get("output") if isinstance(row, dict) else None
        if not isinstance(output, dict):
            continue
        target_id = str(output.get("target_id", "")).strip()
        known = evidence_id_by_target.get(target_id, set())
        issues: list[str] = []
        claim_map = output.get("claim_to_evidence_map")
        if not isinstance(claim_map, list):
            issues.append("claim_map_missing")
        else:
            for claim_index, claim in enumerate(claim_map):
                if not isinstance(claim, dict):
                    issues.append(f"claim_not_object:{claim_index}")
                    continue
                refs = claim.get("evidence_refs")
                if not isinstance(refs, list) or not refs:
                    issues.append(f"missing_refs:{claim_index}")
                    continue
                for ref_index, ref in enumerate(refs):
                    if not isinstance(ref, dict):
                        issues.append(f"ref_not_object:{claim_index}:{ref_index}")
                        continue
                    evidence_id = str(ref.get("evidence_id", "")).strip()
                    if not evidence_id:
                        issues.append(f"missing_evidence_id:{claim_index}:{ref_index}")
                    elif known and evidence_id not in known:
                        issues.append(f"unknown_evidence_id:{claim_index}:{ref_index}")
        if issues:
            status = "fail"
        results.append(
            {
                "target_id": target_id,
                "prompt_id": str(output.get("prompt_id", "")),
                "status": "pass" if not issues else "fail",
                "issues": issues,
            }
        )
    write_json(path, {"run_id": run_id, "status": status, "results": results})


def write_writer_output_auditor_report(
    path: Path, *, run_id: str, gate_report: dict[str, Any]
) -> None:
    result_rows = list(gate_report.get("results") or []) if isinstance(gate_report, dict) else []
    rows: list[dict[str, Any]] = []
    blocked = 0
    for row in result_rows:
        if not isinstance(row, dict):
            continue
        issues = list(row.get("issues") or [])
        overreach = [issue for issue in issues if "unknown_evidence_id" in issue]
        status = "pass" if not overreach else "fail"
        if status == "fail":
            blocked += 1
        rows.append(
            {
                "target_id": str(row.get("target_id", "")),
                "prompt_id": str(row.get("prompt_id", "")),
                "status": status,
                "overreach_issues": overreach,
            }
        )
    report_status = "pass" if blocked == 0 else "fail"
    write_json(
        path,
        {
            "run_id": run_id,
            "status": report_status,
            "blocked_count": blocked,
            "results": rows,
        },
    )
