from __future__ import annotations

import json
from pathlib import Path

from retrieval.writer_host.artifacts import write_evidence_gate_report
from retrieval.writer_host.quality_gate import evaluate_run


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_quality_gate_passes_for_complete_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write(run_dir / "normalization_report.json", {"status": "pass"})
    _write(run_dir / "evidence_synthesizer_gate_report.json", {"status": "pass"})
    _write(run_dir / "writer_output_auditor_report.json", {"status": "pass", "blocked_count": 0})
    _write(
        run_dir / "writer_subagent_outputs" / "merge_validation_report.json",
        {"status": "pass", "entries": []},
    )
    _write(run_dir / "writer_host_run_summary.json", {"status": "completed"})

    report = evaluate_run(run_dir)
    assert report["status"] == "pass"
    assert report["hard_failures"] == []


def test_quality_gate_fails_when_auditor_blocked(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write(run_dir / "normalization_report.json", {"status": "pass"})
    _write(run_dir / "evidence_synthesizer_gate_report.json", {"status": "pass"})
    _write(run_dir / "writer_output_auditor_report.json", {"status": "fail", "blocked_count": 1})
    _write(
        run_dir / "writer_subagent_outputs" / "merge_validation_report.json",
        {"status": "pass", "entries": []},
    )
    _write(run_dir / "writer_host_run_summary.json", {"status": "completed"})

    report = evaluate_run(run_dir)
    assert report["status"] == "fail"
    assert "auditor_pass" in report["hard_failures"]


def test_evidence_gate_fails_on_prompt_example_and_empty_semantics(tmp_path: Path) -> None:
    report_path = tmp_path / "evidence_synthesizer_gate_report.json"
    write_evidence_gate_report(
        report_path,
        run_id="test-run",
        rows=[
            {
                "output": {
                    "target_id": "RET-NEG-001",
                    "prompt_id": "EXAMPLE-001",
                    "hazard": "",
                    "mechanism": "",
                    "mitigation": "",
                    "construct_scope": [],
                    "claim_to_evidence_map": [
                        {
                            "claim_id": "EXAMPLE-001::claim::1",
                            "claim_text": "claim",
                            "evidence_refs": [{"evidence_id": "rust_reference::stmt-1"}],
                        }
                    ],
                }
            }
        ],
        evidence_id_by_target={"RET-NEG-001": {"rust_reference::stmt-1"}},
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    issues = report["results"][0]["issues"]
    assert "prompt_example_contamination" in issues
    assert "hazard_empty" in issues
    assert "mechanism_empty" in issues
    assert "mitigation_empty" in issues
    assert "construct_scope_empty" in issues
