from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from retrieval.guidelines.build_runner import run_guidelines_build
from retrieval.services.guidelines_projection import run_m15_projection


def _extract_example_failures(stdout_tail: str) -> list[dict[str, str]]:
    lines = stdout_tail.splitlines()
    failures: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if raw_line.startswith("📍 "):
            if current is not None:
                failures.append(current)
            current = {"location": raw_line.removeprefix("📍 ").strip()}
            continue
        if current is None:
            continue
        if "Compilation failed unexpectedly" in line:
            current["kind"] = "compile_failed"
        elif "Compilation succeeded but produced warnings" in line:
            current["kind"] = "warnings_as_failures"
        elif line.startswith("Parent:"):
            current["parent"] = line.removeprefix("Parent:").strip()
        elif "ambiguous interpretation" in line:
            current["reason"] = "ambiguous_reference_range_pattern"
        elif "deprecated method" in line:
            current["reason"] = "deprecated_api"
    if current is not None:
        failures.append(current)
    return failures


def _offline_build_failures(stderr_tail: str) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for line in stderr_tail.splitlines():
        text = line.strip()
        if not text:
            continue
        if "references 'fls_UNRESOLVED'" in text:
            failures.append({"kind": "fls_unresolved", "detail": text})
        elif "non-existent FLS ID" in text:
            failures.append({"kind": "fls_missing", "detail": text})
        elif "bibliography" in text.lower():
            failures.append({"kind": "bibliography_validation", "detail": text})
    return failures


def run_conformance(
    *, repo_root: Path, report_dir: Path, mode: str = "publishable"
) -> dict[str, Any]:
    report_dir.mkdir(parents=True, exist_ok=True)
    m15_code, m15_stdout, m15_stderr = run_m15_projection(repo_root, report_dir)
    build_env = (
        {"OPENCODE_ALLOW_REVIEW_UNRESOLVED_FLS": "1"}
        if str(mode).strip().lower() == "review"
        else None
    )
    build_code, build_stdout, build_stderr, versions = run_guidelines_build(
        repo_root=repo_root,
        offline=True,
        extra_env=build_env,
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
        "mode": mode,
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
        "failure_taxonomy": {
            "extract_examples": _extract_example_failures((m15_stdout or "")[-2000:]),
            "offline_build": _offline_build_failures((build_stderr or "")[-2000:]),
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
