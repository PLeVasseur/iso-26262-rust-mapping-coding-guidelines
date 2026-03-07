from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.publish_loader import load_publish_payload  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_load_publish_payload_joins_atom_outputs_by_draft_id(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_json(run_dir / "writer_quality_gate_report.json", {"status": "pass"})
    _write_jsonl(
        run_dir / "drafts.jsonl",
        [
            {
                "draft_id": "draft::RET-ISSUE-006::atom::lint-enforcement",
                "target_id": "RET-ISSUE-006",
                "atom_id": "RET-ISSUE-006::atom::lint-enforcement",
                "claim_to_evidence_map": [{"claim_text": "claim"}],
            }
        ],
    )
    subagent = run_dir / "writer_subagent_outputs"
    for name, output in {
        "amplification_author": {"guideline_amplification_text": "body"},
        "rationale_author": {"rationale_text": "why"},
        "example_author": {"non_compliant_narrative": "bad"},
        "metadata_citation_curator": {"tags": ["diagnostics"]},
    }.items():
        _write_jsonl(
            subagent / f"{name}.jsonl",
            [
                {
                    "target_id": "RET-ISSUE-006",
                    "draft_id": "draft::RET-ISSUE-006::atom::lint-enforcement",
                    "output": output,
                }
            ],
        )

    payload = load_publish_payload(run_dir=run_dir, publishable=True)

    assert payload["draft_count"] == 1
    assert payload["draft_rows"][0]["draft"]["atom_id"] == "RET-ISSUE-006::atom::lint-enforcement"
