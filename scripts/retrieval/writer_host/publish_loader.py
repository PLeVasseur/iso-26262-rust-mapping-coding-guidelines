from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _by_draft(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        draft_id = str(row.get("draft_id", "")).strip()
        output = row.get("output")
        if draft_id and isinstance(output, dict):
            out[draft_id] = output
    return out


def load_publish_payload(*, run_dir: Path, publishable: bool) -> dict[str, Any]:
    drafts_path = run_dir / "drafts.jsonl"
    if not drafts_path.exists():
        raise RuntimeError(f"missing publish artifact: {drafts_path}")

    gate_path = run_dir / "writer_quality_gate_report.json"
    gate = {}
    if gate_path.exists():
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
    if publishable and str(gate.get("status", "")).lower() != "pass":
        raise RuntimeError("publishable mode requires passing writer_quality_gate_report.json")

    subagent_root = run_dir / "writer_subagent_outputs"
    if not subagent_root.exists():
        raise RuntimeError(f"missing publish artifact directory: {subagent_root}")

    drafts = _read_jsonl(drafts_path)
    if not drafts:
        raise RuntimeError("drafts.jsonl is empty")

    amplification = _by_draft(subagent_root / "amplification_author.jsonl")
    rationale = _by_draft(subagent_root / "rationale_author.jsonl")
    examples = _by_draft(subagent_root / "example_author.jsonl")
    metadata = _by_draft(subagent_root / "metadata_citation_curator.jsonl")

    rows: list[dict[str, Any]] = []
    for draft in drafts:
        target_id = str(draft.get("target_id", "")).strip()
        draft_id = str(draft.get("draft_id", "")).strip()
        if not target_id:
            raise RuntimeError("draft missing target_id")
        if not draft_id:
            raise RuntimeError(f"draft missing draft_id for {target_id}")
        if draft_id not in amplification:
            raise RuntimeError(f"missing amplification output for {draft_id}")
        if draft_id not in rationale:
            raise RuntimeError(f"missing rationale output for {draft_id}")
        if draft_id not in examples:
            raise RuntimeError(f"missing example output for {draft_id}")
        if draft_id not in metadata:
            raise RuntimeError(f"missing metadata output for {draft_id}")
        claim_map = draft.get("claim_to_evidence_map")
        if not isinstance(claim_map, list) or not claim_map:
            raise RuntimeError(f"draft claim map empty for {draft_id}")
        rows.append(
            {
                "draft": draft,
                "amplification": amplification[draft_id],
                "rationale": rationale[draft_id],
                "examples": examples[draft_id],
                "metadata": metadata[draft_id],
            }
        )

    return {
        "run_dir": str(run_dir),
        "gate": gate,
        "draft_count": len(rows),
        "draft_rows": rows,
    }
