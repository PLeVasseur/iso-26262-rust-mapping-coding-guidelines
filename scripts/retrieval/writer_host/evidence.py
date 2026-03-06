from __future__ import annotations

import json
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retrieval.services import query_service
from retrieval.writer_host.fusion import fuse_ranked_lists
from retrieval.writer_host.manifest import write_manifest
from retrieval.writer_host.targets import load_targets_manifest


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
        raise RuntimeError(f"query failed for {prompt_id} mode={mode} corpus={corpus}: exit={code}")
    after = set(save_response_dir.glob("*.json"))
    created = sorted(after - before, key=lambda p: p.stat().st_mtime)
    if not created:
        created = sorted(
            save_response_dir.glob(f"*{_slug(prompt_id)}*{mode}*.json"),
            key=lambda p: p.stat().st_mtime,
        )
    if not created:
        raise RuntimeError(f"query output missing for {prompt_id} mode={mode} corpus={corpus}")
    latest = created[-1]
    payload = json.loads(latest.read_text(encoding="utf-8"))
    return payload, latest


def _extract_ranked_rows(
    payload: dict[str, Any], *, mode: str, corpus: str
) -> list[dict[str, Any]]:
    response = payload.get("response") if isinstance(payload, dict) else {}
    rows = list(response.get("rows") or []) if isinstance(response, dict) else []
    ranked: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        raw_statement_id = str(row.get("statement_id") or row.get("top_statement_id") or "").strip()
        if not raw_statement_id:
            continue
        statement_id = f"{corpus}::{raw_statement_id}"
        ranked.append(
            {
                "statement_id": statement_id,
                "raw_statement_id": raw_statement_id,
                "corpus": corpus,
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
    run_id = str(getattr(args, "run_id", "") or "").strip() or f"writer_evidence_{_now_slug()}"
    report_root = str(getattr(args, "report_root", "") or "").strip()
    run_dir = (
        Path(report_root) if report_root else root / ".cache" / "sqlite_kb" / "reports" / run_id
    ).resolve()
    evidence_root = run_dir / "evidence_bundle"
    run_dir.mkdir(parents=True, exist_ok=True)
    evidence_root.mkdir(parents=True, exist_ok=True)

    corpora = _parse_csv(str(getattr(args, "corpora", "") or ""))
    if not corpora:
        raise RuntimeError("writer-evidence requires at least one corpus")
    profile_path = str(getattr(args, "profile_path", "") or "")

    targets_manifest_raw = str(getattr(args, "targets_manifest", "") or "").strip()
    if not targets_manifest_raw:
        raise RuntimeError("writer-evidence requires --targets-manifest")
    targets_manifest = Path(targets_manifest_raw).resolve()
    targets = load_targets_manifest(targets_manifest)
    if not targets:
        raise RuntimeError(f"writer-evidence found no targets in {targets_manifest}")

    requested_modes = _parse_csv(str(getattr(args, "modes", "lexical,semantic,hybrid") or ""))
    modes = [mode for mode in requested_modes if mode in {"lexical", "semantic", "hybrid"}]
    if not modes:
        modes = ["lexical", "semantic", "hybrid"]

    top_k = int(getattr(args, "top_k", 20) or 20)
    top_n = int(getattr(args, "top_n", 12) or 12)
    rrf_k = int(getattr(args, "rrf_k", 60) or 60)
    rank_window = int(getattr(args, "rank_window", 100) or 100)
    allow_degraded = bool(getattr(args, "allow_degraded", False))

    target_rows: list[dict[str, Any]] = []
    retrieval_errors: list[dict[str, Any]] = []
    degraded_mode_failures: list[dict[str, Any]] = []
    modes_executed: set[str] = set()

    for target in targets:
        target_id = str(target.get("prompt_id", "")).strip()
        query_text = str(target.get("query_text", "")).strip()
        expected_row_markers = list(target.get("expected_row_markers") or [])
        per_corpus: dict[str, dict[str, Any]] = {}
        combined_selected: list[dict[str, Any]] = []

        for corpus in corpora:
            corpus_evidence_dir = evidence_root / corpus
            corpus_evidence_dir.mkdir(parents=True, exist_ok=True)
            per_mode_rows: dict[str, list[dict[str, Any]]] = {}
            mode_artifacts: dict[str, str] = {}
            mode_errors: dict[str, str] = {}

            for mode in modes:
                prompt_key = f"{corpus}::{target_id}"
                try:
                    payload, artifact_path = _query_target(
                        root=root,
                        corpus=corpus,
                        profile_path=profile_path,
                        prompt_id=prompt_key,
                        query_text=query_text,
                        mode=mode,
                        top_k=top_k,
                        save_response_dir=corpus_evidence_dir,
                    )
                    per_mode_rows[mode] = _extract_ranked_rows(payload, mode=mode, corpus=corpus)
                    mode_artifacts[mode] = str(artifact_path)
                    modes_executed.add(mode)
                except Exception as exc:
                    mode_errors[mode] = str(exc)

            if not per_mode_rows:
                retrieval_errors.append(
                    {
                        "target_id": target_id,
                        "corpus": corpus,
                        "error": "all_modes_failed",
                        "mode_errors": mode_errors,
                    }
                )
                if not allow_degraded:
                    raise RuntimeError(
                        f"writer-evidence failed for {target_id} corpus={corpus}: {mode_errors}"
                    )
                continue

            if mode_errors:
                degraded_mode_failures.append(
                    {
                        "target_id": target_id,
                        "corpus": corpus,
                        "error": "partial_mode_failure",
                        "mode_errors": mode_errors,
                    }
                )
                if not allow_degraded:
                    raise RuntimeError(
                        "writer-evidence mode failure for "
                        f"{target_id} corpus={corpus}; re-run with --allow-degraded: {mode_errors}"
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
                    "raw_statement_id": str(row.get("raw_statement_id", "")),
                    "corpus": str(row.get("corpus", corpus)),
                    "source_anchor": str(row.get("source_anchor", "")),
                    "doc_id": str(row.get("doc_id", "")),
                    "statement_text": str(row.get("statement_text", "")),
                    "score": float(row.get("fused_score", 0.0)),
                    "mode_hits": list(row.get("mode_hits") or []),
                }
                for row in selected_rows
            ]
            combined_selected.extend(selected_evidence)
            per_corpus[corpus] = {
                "mode_artifacts": mode_artifacts,
                "mode_errors": mode_errors,
                "decision": decision,
                "selected_evidence": selected_evidence,
            }

        combined_selected.sort(
            key=lambda row: (-float(row.get("score", 0.0)), str(row.get("statement_id", "")))
        )
        target_rows.append(
            {
                "target_id": target_id,
                "prompt_id": target_id,
                "query_text": query_text,
                "expected_row_markers": expected_row_markers,
                "per_corpus": per_corpus,
                "selected_evidence": combined_selected,
                "selected_evidence_ids": [
                    str(row.get("statement_id", "")).strip()
                    for row in combined_selected
                    if str(row.get("statement_id", "")).strip()
                ],
            }
        )

    manifest = {
        "manifest_id": f"writer_evidence_manifest_{_now_slug()}",
        "run_id": run_id,
        "corpora": corpora,
        "profile_path": profile_path,
        "targets_manifest": str(targets_manifest),
        "modes_requested": modes,
        "modes_executed": sorted(modes_executed),
        "errors": retrieval_errors,
        "degraded_mode_failures": degraded_mode_failures,
        "degraded": bool(retrieval_errors or degraded_mode_failures),
        "targets": target_rows,
    }
    output_raw = str(getattr(args, "output", "") or "").strip()
    output_path = (
        Path(output_raw).resolve() if output_raw else run_dir / "writer_evidence_manifest.json"
    )
    write_manifest(output_path, manifest)
    print(output_path)
    return 0
