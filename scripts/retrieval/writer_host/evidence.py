from __future__ import annotations

import json
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retrieval.services import query_service
from retrieval.writer_host.fusion import fuse_ranked_lists
from retrieval.writer_host.manifest import write_manifest
from retrieval.writer_host.targets import load_prompts


def _now_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _parse_csv(raw: str) -> list[str]:
    return [value.strip() for value in str(raw).split(",") if value.strip()]


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
) -> tuple[dict[str, Any], Path]:
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
        raise RuntimeError(f"query failed for {prompt_id} mode={mode}: exit={code}")
    after = set(save_response_dir.glob("*.json"))
    created = sorted(after - before, key=lambda p: p.stat().st_mtime)
    if not created:
        created = sorted(
            save_response_dir.glob(f"*{_slug(prompt_id)}*{mode}*.json"),
            key=lambda p: p.stat().st_mtime,
        )
    if not created:
        raise RuntimeError(f"query output missing for {prompt_id} mode={mode}")
    latest = created[-1]
    payload = json.loads(latest.read_text(encoding="utf-8"))
    return payload, latest


def _extract_ranked_rows(payload: dict[str, Any], *, mode: str) -> list[dict[str, Any]]:
    response = payload.get("response") if isinstance(payload, dict) else {}
    rows = list(response.get("rows") or []) if isinstance(response, dict) else []
    ranked: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        statement_id = str(row.get("statement_id") or row.get("top_statement_id") or "").strip()
        if not statement_id:
            continue
        ranked.append(
            {
                "statement_id": statement_id,
                "source_anchor": str(
                    row.get("source_anchor") or row.get("top_source_anchor") or ""
                ).strip(),
                "doc_id": str(row.get("doc_id") or row.get("top_doc_id") or "").strip(),
                "statement_text": str(
                    row.get("statement_text") or row.get("chunk_text") or ""
                ).strip(),
                "score": float(row.get("final_score") or 0.0),
                "rank": rank,
                "mode": mode,
            }
        )
    return ranked


def run(args: Namespace, *, root: Path) -> int:
    corpus = str(getattr(args, "corpus", "rust_reference") or "rust_reference")
    profile_path = str(getattr(args, "profile_path", "") or "")
    run_id = str(getattr(args, "run_id", "") or "").strip() or f"writer_evidence_{_now_slug()}"
    report_root = str(getattr(args, "report_root", "") or "").strip()
    run_dir = (
        Path(report_root) if report_root else root / ".cache" / "sqlite_kb" / "reports" / run_id
    ).resolve()
    evidence_dir = run_dir / "evidence_bundle"
    run_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    query_testset_path = (
        root
        / str(
            getattr(
                args,
                "query_testset_path",
                "data/query_testsets/rust_reference_table1_retrieval_eval.yaml",
            )
        )
    ).resolve()
    prompts = load_prompts(query_testset_path)
    prompt_lookup = {str(row.get("prompt_id", "")).strip(): row for row in prompts}

    target_ids = _parse_csv(str(getattr(args, "targets", "") or ""))
    if not target_ids:
        raise RuntimeError("writer-evidence requires --targets")

    requested_modes = _parse_csv(str(getattr(args, "modes", "lexical,semantic,hybrid") or ""))
    if not requested_modes:
        requested_modes = ["lexical", "semantic", "hybrid"]
    allowed = {"lexical", "semantic", "hybrid"}
    modes = [mode for mode in requested_modes if mode in allowed]
    if not modes:
        raise RuntimeError("writer-evidence requires at least one valid mode")

    top_k = int(getattr(args, "top_k", 20) or 20)
    top_n = int(getattr(args, "top_n", 8) or 8)
    rrf_k = int(getattr(args, "rrf_k", 60) or 60)
    rank_window = int(getattr(args, "rank_window", 100) or 100)
    allow_degraded = bool(getattr(args, "allow_degraded", False))

    target_rows: list[dict[str, Any]] = []
    modes_executed: set[str] = set()
    degraded_targets: list[str] = []

    for target_id in target_ids:
        prompt = prompt_lookup.get(target_id)
        if not isinstance(prompt, dict):
            raise RuntimeError(f"prompt_id not found in query testset: {target_id}")
        query_text = str(prompt.get("query_text", "")).strip()
        expected_row_markers = list(prompt.get("expected_row_markers") or [])

        per_mode_rows: dict[str, list[dict[str, Any]]] = {}
        mode_artifacts: dict[str, str] = {}
        mode_errors: dict[str, str] = {}

        for mode in modes:
            try:
                payload, artifact_path = _query_target(
                    root=root,
                    corpus=corpus,
                    profile_path=profile_path,
                    prompt_id=target_id,
                    query_text=query_text,
                    mode=mode,
                    top_k=top_k,
                    save_response_dir=evidence_dir,
                )
                per_mode_rows[mode] = _extract_ranked_rows(payload, mode=mode)
                mode_artifacts[mode] = str(artifact_path)
                modes_executed.add(mode)
            except Exception as exc:
                mode_errors[mode] = str(exc)

        if not per_mode_rows:
            raise RuntimeError(
                f"writer-evidence failed for {target_id}: no retrieval modes succeeded ({mode_errors})"
            )
        if mode_errors:
            degraded_targets.append(target_id)
            if not allow_degraded:
                raise RuntimeError(
                    f"writer-evidence mode failure for {target_id}; re-run with --allow-degraded: {mode_errors}"
                )

        selected_rows, decision = fuse_ranked_lists(
            ranked_rows_by_mode=per_mode_rows,
            rrf_k=rrf_k,
            rank_window=rank_window,
            top_n=top_n,
        )
        selected_evidence = [
            {
                "statement_id": str(row.get("statement_id", "")),
                "source_anchor": str(row.get("source_anchor", "")),
                "doc_id": str(row.get("doc_id", "")),
                "statement_text": str(row.get("statement_text", "")),
                "score": float(row.get("fused_score", 0.0)),
                "mode_hits": list(row.get("mode_hits") or []),
            }
            for row in selected_rows
        ]

        target_rows.append(
            {
                "target_id": target_id,
                "prompt_id": target_id,
                "query_text": query_text,
                "expected_row_markers": expected_row_markers,
                "mode_artifacts": mode_artifacts,
                "mode_errors": mode_errors,
                "decision": decision,
                "selected_evidence": selected_evidence,
                "selected_evidence_ids": [
                    str(row.get("statement_id", "")).strip()
                    for row in selected_evidence
                    if str(row.get("statement_id", "")).strip()
                ],
            }
        )

    manifest = {
        "manifest_id": f"writer_evidence_manifest_{_now_slug()}",
        "run_id": run_id,
        "corpus": corpus,
        "profile_path": profile_path,
        "query_testset_path": str(query_testset_path),
        "modes_requested": modes,
        "modes_executed": sorted(modes_executed),
        "degraded": bool(degraded_targets),
        "degraded_targets": degraded_targets,
        "targets": target_rows,
    }
    output_raw = str(getattr(args, "output", "") or "").strip()
    output_path = (
        Path(output_raw).resolve() if output_raw else run_dir / "writer_evidence_manifest.json"
    )
    write_manifest(output_path, manifest)
    print(output_path)
    return 0
