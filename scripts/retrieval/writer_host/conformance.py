from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from retrieval.guidelines.build_runner import run_guidelines_build
from retrieval.services.guidelines_projection import run_m15_projection


def run_conformance(*, repo_root: Path, report_dir: Path) -> dict[str, Any]:
    report_dir.mkdir(parents=True, exist_ok=True)
    m15_code, m15_stdout, m15_stderr = run_m15_projection(repo_root, report_dir)
    build_code, build_stdout, build_stderr, versions = run_guidelines_build(
        repo_root=repo_root,
        offline=True,
    )
    metrics: dict[str, int] = {}
    metrics_path = report_dir / "annotation_policy_metrics.json"
    if metrics_path.exists():
        try:
            loaded = json.loads(metrics_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                metrics = {str(k): int(v) for k, v in loaded.items()}
        except Exception:
            metrics = {}
    skip_without_justification = int(metrics.get("miri_skip_without_justification_count", 0))
    policy_ok = skip_without_justification == 0
    passed = int(m15_code) == 0 and int(build_code) == 0 and policy_ok
    report = {
        "status": "pass" if passed else "fail",
        "checks": {
            "m15_extract_examples": int(m15_code) == 0,
            "offline_build": int(build_code) == 0,
            "annotation_policy": policy_ok,
        },
        "returncodes": {
            "m15_extract_examples": int(m15_code),
            "offline_build": int(build_code),
        },
        "versions": versions,
        "annotation_policy_metrics": metrics,
        "logs": {
            "m15_stdout_tail": (m15_stdout or "")[-2000:],
            "m15_stderr_tail": (m15_stderr or "")[-2000:],
            "build_stdout_tail": (build_stdout or "")[-2000:],
            "build_stderr_tail": (build_stderr or "")[-2000:],
        },
        "remediation": [
            "Run extract_rust_examples test command and fix failing examples",
            "Run make.py --offline in guidelines repo and fix build errors",
            "Avoid :miri: skip without explicit justification fields",
        ],
    }
    (report_dir / "writer_conformance_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return report
