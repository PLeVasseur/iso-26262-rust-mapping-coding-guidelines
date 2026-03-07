from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_run(run_dir: Path) -> dict[str, Any]:
    normalization = _load_json(run_dir / "normalization_report.json")
    gate = _load_json(run_dir / "evidence_synthesizer_gate_report.json")
    auditor = _load_json(run_dir / "writer_output_auditor_report.json")
    merge = _load_json(run_dir / "writer_subagent_outputs" / "merge_validation_report.json")
    summary = _load_json(run_dir / "writer_host_run_summary.json")
    editorial_path = run_dir / "editorial_review_report.json"
    editorial = _load_json(editorial_path) if editorial_path.exists() else {}

    checks = {
        "normalization_pass": str(normalization.get("status", "")) == "pass",
        "evidence_gate_pass": str(gate.get("status", "")) == "pass",
        "auditor_pass": str(auditor.get("status", "")) == "pass"
        and int(auditor.get("blocked_count", 0) or 0) == 0,
        "merge_pass": str(merge.get("status", "")) == "pass",
        "run_completed": str(summary.get("status", "")).startswith("completed"),
    }
    warning_count = sum(
        len(list(entry.get("warnings") or [])) for entry in list(merge.get("entries") or [])
    )
    hard_failures = [name for name, ok in checks.items() if not ok]
    status = "pass" if not hard_failures else "fail"
    conformance_path = run_dir / "writer_conformance_report.json"
    review_ready = {"status": "not_evaluated", "checks": {}}
    if conformance_path.exists():
        conformance = _load_json(conformance_path)
        conformance_checks = dict(conformance.get("checks") or {})
        review_ready = {
            "status": "pass" if str(conformance.get("status", "")) == "pass" else "fail",
            "checks": conformance_checks,
            "report_path": str(conformance_path),
        }
    editorial_status = {
        "status": "not_evaluated",
        "blocked_count": 0,
        "review_count": 0,
        "report_path": "",
    }
    if editorial:
        editorial_status = {
            "status": "pass" if str(editorial.get("status", "")) == "pass" else "review",
            "blocked_count": int(editorial.get("blocked_count", 0) or 0),
            "review_count": int(editorial.get("review_count", 0) or 0),
            "report_path": str(editorial_path),
        }
    return {
        "status": status,
        "run_dir": str(run_dir),
        "checks": checks,
        "lifecycle": {
            "writer_complete": status,
            "editorially_reviewable": editorial_status,
            "review_ready": review_ready,
            "next_required_gate": "writer_conformance"
            if review_ready["status"] == "not_evaluated"
            else "none",
        },
        "hard_failures": hard_failures,
        "warning_count": warning_count,
    }


def write_quality_gate_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")
