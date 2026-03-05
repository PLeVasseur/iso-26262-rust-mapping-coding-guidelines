from __future__ import annotations

import json
import os
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from retrieval.services import query_service
from retrieval.writer_host.artifacts import (
    write_evidence_gate_report,
    write_json,
    write_jsonl,
    write_normalization_report,
    write_writer_output_auditor_report,
    write_writer_outputs,
)
from retrieval.writer_host.contracts import REQUIRED_ROLES, build_contract_snapshot, load_contracts
from retrieval.writer_host.retry import run_role_with_retry
from retrieval.writer_host.roles import (
    build_role_prompt,
    extract_claim_map,
    extract_construct_terms,
)
from retrieval.writer_host.validation import validate_role_output


def _now_run_id() -> str:
    return f"writer_host_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"


def _load_prompt_catalog(path: Path) -> dict[str, dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    prompts = payload.get("prompts") if isinstance(payload, dict) else None
    result: dict[str, dict[str, Any]] = {}
    if isinstance(prompts, list):
        for row in prompts:
            if not isinstance(row, dict):
                continue
            prompt_id = str(row.get("prompt_id", "")).strip()
            if prompt_id:
                result[prompt_id] = row
    return result


def _parse_targets(raw: str) -> list[str]:
    values = [piece.strip() for piece in str(raw).split(",")]
    return [value for value in values if value]


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")


def _query_target(
    *,
    root: Path,
    corpus: str,
    profile_path: str,
    prompt_id: str,
    query_text: str,
    mode: str,
    top_k: int,
    save_response_dir: Path,
) -> dict[str, Any]:
    before = set(save_response_dir.glob("*.json"))
    args = Namespace(
        corpus=corpus,
        profile_path=profile_path,
        extra_args=[
            "--mode",
            mode,
            "--prompt-id",
            prompt_id,
            "--query-text",
            query_text,
            "--top-k",
            str(top_k),
            "--include-score-breakdown",
            "--save-response-dir",
            str(save_response_dir),
        ],
    )
    code = query_service.run(args, root=root)
    if int(code) != 0:
        raise RuntimeError(f"query failed for {prompt_id}: exit={code}")
    after = set(save_response_dir.glob("*.json"))
    created = sorted(after - before, key=lambda p: p.stat().st_mtime)
    if not created:
        pattern = f"*{_slug(prompt_id)}*"
        created = sorted(save_response_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
    if not created:
        raise RuntimeError(f"query output missing for {prompt_id}")
    latest = created[-1]
    return json.loads(latest.read_text(encoding="utf-8"))


def run(args: Namespace, *, root: Path) -> int:
    run_id = str(getattr(args, "run_id", "") or "").strip() or _now_run_id()
    report_root = str(getattr(args, "report_root", "") or "").strip()
    run_dir = (
        Path(report_root) if report_root else root / ".cache" / "sqlite_kb" / "reports" / run_id
    ).resolve()
    writer_root = run_dir / "writer_subagent_outputs"
    evidence_dir = run_dir / "evidence_bundle"
    run_dir.mkdir(parents=True, exist_ok=True)
    writer_root.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    contract_path = (
        root / str(getattr(args, "contract_path", "config/s0/writer_prompt_contracts.yaml"))
    ).resolve()
    contracts = load_contracts(contract_path)
    contract_snapshot = build_contract_snapshot(contracts)

    if bool(getattr(args, "dry_run", False)):
        write_writer_outputs(
            writer_root=writer_root,
            role_rows={role: [] for role in REQUIRED_ROLES},
            contract_snapshot=contract_snapshot,
            invocation_trace=[],
            merge_validation_report={"run_id": run_id, "status": "pass", "notes": ["dry-run"]},
        )
        write_json(
            run_dir / "writer_host_run_summary.json", {"run_id": run_id, "status": "dry_run"}
        )
        return 0

    target_ids = _parse_targets(str(getattr(args, "targets", "") or ""))
    if not target_ids:
        raise RuntimeError("writer-host-run requires --targets")

    prompt_catalog = _load_prompt_catalog(
        (
            root
            / str(
                getattr(
                    args,
                    "query_testset_path",
                    "data/query_testsets/rust_reference_table1_retrieval_eval.yaml",
                )
            )
        ).resolve()
    )
    role_cfg = contracts.get("roles") if isinstance(contracts.get("roles"), dict) else {}
    max_retries = int(getattr(args, "max_retries", 2) or 2)
    mode = str(getattr(args, "query_mode", "lexical") or "lexical")
    top_k = int(getattr(args, "top_k", 20) or 20)
    corpus = str(getattr(args, "corpus", "rust_reference") or "rust_reference")
    profile_path = str(getattr(args, "profile_path", "") or "")
    model = str(getattr(args, "model", "") or os.environ.get("WRITER_MODEL", "")).strip() or None
    agent = str(getattr(args, "agent", "") or os.environ.get("WRITER_AGENT", "")).strip() or None

    role_rows: dict[str, list[dict[str, Any]]] = {role: [] for role in REQUIRED_ROLES}
    invocation_trace: list[dict[str, Any]] = []
    validation_entries: list[dict[str, Any]] = []
    drafts: list[dict[str, Any]] = []
    evidence_ids_by_target: dict[str, set[str]] = {}

    for target_id in target_ids:
        prompt = prompt_catalog.get(target_id)
        if not isinstance(prompt, dict):
            raise RuntimeError(f"prompt_id not found in query testset: {target_id}")
        query_text = str(prompt.get("query_text", "")).strip()
        expected_row_markers = list(prompt.get("expected_row_markers") or [])
        expected_row_marker = str(expected_row_markers[0]) if expected_row_markers else ""
        query_payload = _query_target(
            root=root,
            corpus=corpus,
            profile_path=profile_path,
            prompt_id=target_id,
            query_text=query_text,
            mode=mode,
            top_k=top_k,
            save_response_dir=evidence_dir,
        )
        response = query_payload.get("response") if isinstance(query_payload, dict) else {}
        rows = list(response.get("rows") or []) if isinstance(response, dict) else []
        evidence_rows: list[dict[str, Any]] = []
        for row in rows[:5]:
            if not isinstance(row, dict):
                continue
            statement_id = str(row.get("statement_id") or row.get("top_statement_id") or "").strip()
            if not statement_id:
                continue
            evidence_rows.append(
                {
                    "statement_id": statement_id,
                    "source_anchor": str(
                        row.get("source_anchor") or row.get("top_source_anchor") or ""
                    ),
                    "score": float(row.get("final_score") or 0.0),
                }
            )
        evidence_ids_by_target[target_id] = {
            str(row.get("statement_id", "")) for row in evidence_rows
        }

        prior_outputs: dict[str, dict[str, Any]] = {}
        for role_name in REQUIRED_ROLES:
            role_contract = role_cfg.get(role_name) if isinstance(role_cfg, dict) else {}
            if not isinstance(role_contract, dict):
                raise RuntimeError(f"missing role config: {role_name}")
            role_contract_dict: dict[str, Any] = dict(role_contract)

            prompt_text, prompt_hash = build_role_prompt(
                role_name=role_name,
                target_id=target_id,
                prompt_id=target_id,
                table1_row=expected_row_marker,
                query_text=query_text,
                evidence_rows=evidence_rows,
                prior_outputs=prior_outputs,
                role_contract=role_contract_dict,
            )

            def _validate(output: dict[str, Any]) -> list[str]:
                return validate_role_output(
                    role_name=role_name,
                    output=output,
                    role_contract=role_contract_dict,
                    evidence_ids=evidence_ids_by_target[target_id],
                )

            outcome = run_role_with_retry(
                role_name=role_name,
                prompt=prompt_text,
                validate_output=_validate,
                max_retries=max_retries,
                model=model,
                agent=agent,
            )
            prior_outputs[role_name] = outcome.output
            role_rows[role_name].append(
                {
                    "target_id": target_id,
                    "draft_id": f"draft::{target_id}",
                    "attempts": outcome.attempts,
                    "status": "pass" if not outcome.violations else "review",
                    "violations": outcome.violations,
                    "output": outcome.output,
                    "prompt_hash": prompt_hash,
                }
            )
            invocation_trace.append(
                {
                    "target_id": target_id,
                    "role": role_name,
                    "session_id": outcome.session_id,
                    "model": model,
                    "agent": agent,
                    "prompt_hash": prompt_hash,
                    "attempts": outcome.attempts,
                    "violations_remaining": outcome.violations,
                    "oscillation_detected": outcome.oscillation_detected,
                    "diminishing_returns": outcome.diminishing_returns,
                    "budget_exhausted": outcome.budget_exhausted,
                }
            )
            validation_entries.append(
                {
                    "target_id": target_id,
                    "role": role_name,
                    "attempts": outcome.attempts,
                    "violations": outcome.violations,
                }
            )

        synth = prior_outputs.get("evidence_synthesizer", {})
        drafts.append(
            {
                "draft_id": f"draft::{target_id}",
                "target_id": target_id,
                "target_prompt_id": target_id,
                "status": "drafted",
                "construct_terms": extract_construct_terms(synth),
                "claim_to_evidence_map": extract_claim_map(synth),
            }
        )

    write_writer_outputs(
        writer_root=writer_root,
        role_rows=role_rows,
        contract_snapshot=contract_snapshot,
        invocation_trace=invocation_trace,
        merge_validation_report={
            "run_id": run_id,
            "status": "pass"
            if not any(bool(entry.get("violations")) for entry in validation_entries)
            else "fail",
            "target_count": len(target_ids),
        },
    )
    write_json(
        run_dir / "role_validation_report.json", {"run_id": run_id, "entries": validation_entries}
    )
    write_jsonl(run_dir / "drafts.jsonl", drafts)
    write_normalization_report(
        run_dir / "normalization_report.json",
        run_id=run_id,
        rows=role_rows["evidence_synthesizer"],
    )
    write_evidence_gate_report(
        run_dir / "evidence_synthesizer_gate_report.json",
        run_id=run_id,
        rows=role_rows["evidence_synthesizer"],
        evidence_id_by_target=evidence_ids_by_target,
    )
    gate_report = json.loads(
        (run_dir / "evidence_synthesizer_gate_report.json").read_text(encoding="utf-8")
    )
    write_writer_output_auditor_report(
        run_dir / "writer_output_auditor_report.json",
        run_id=run_id,
        gate_report=gate_report,
    )

    has_violations = any(bool(entry.get("violations")) for entry in validation_entries)

    write_json(
        run_dir / "writer_host_run_summary.json",
        {
            "run_id": run_id,
            "status": "completed" if not has_violations else "completed_with_violations",
            "target_ids": target_ids,
            "run_dir": str(run_dir),
            "normalization_report": str(run_dir / "normalization_report.json"),
            "evidence_gate_report": str(run_dir / "evidence_synthesizer_gate_report.json"),
        },
    )
    return 0 if not has_violations else 2
