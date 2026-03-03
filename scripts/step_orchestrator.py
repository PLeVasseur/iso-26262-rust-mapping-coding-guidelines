#!/usr/bin/env python3
"""Step-level orchestrator for the v17.2 plan.

Executes one step per invocation, then stops for human review.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PIPELINE_ROOT = Path(__file__).resolve().parent.parent
_config_dir = os.environ.get("OPENCODE_CONFIG_DIR", "")
if _config_dir:
    PLAN_DIR = Path(_config_dir) / "plans" / "v17_2_plan"
else:
    PLAN_DIR = PIPELINE_ROOT / "plans" / "v17_2_plan"

STEPS = list(range(15))  # 0..14 for v17.2
STEP_DEPS: dict[int, list[int]] = {
    0: [],
    1: [0],
    2: [0],
    3: [2],
    4: [2, 3],
    5: [0],
    6: [0],
    7: [3, 6],
    8: [4, 7],
    9: [0],
    10: [0],
    11: [10],
    12: [2],
    13: [8, 12],
    14: list(range(14)),
}
STEP_TIMEOUTS: dict[int, int] = {
    0: 3600,
    1: 3600,
    2: 3600,
    3: 1800,
    4: 3000,
    5: 1800,
    6: 2400,
    7: 3600,
    8: 3600,
    9: 7200,
    10: 3600,
    11: 3600,
    12: 1800,
    13: 2400,
    14: 7200,
}
CP_A_AFTER_STEP = 4
CP_B_AFTER_STEP = 11
CP_C_AFTER_STEP = 13

STEP_PREREQ_REF_OVERRIDES: dict[int, dict[str, str]] = {
    8: {
        "__init__.py": "validation/__init__.py",
    },
    9: {
        "cache/sqlite_kb/reports/phase_a_opencode_v3_exec2/targets.json": ".cache/sqlite_kb/reports/phase_a_opencode_v3_exec2/targets.json",
    },
}
STEP_OPTIONAL_PREREQS: dict[int, set[str]] = {
    8: {"retry_pilot_results.json"},
    9: {
        "scripts/rendering_v2/rst_renderer.py",
        "scripts/validation_v2/conformance.py",
    },
}
STEP_WAIVER_RULES: dict[int, dict[str, str]] = {
    8: {"rendering/bibliography.py": "STEP7-BIB-PATH"},
}

OPENCODE_SERVER = "http://localhost:4096"
CONTRACTS_DIR = PIPELINE_ROOT / ".cache" / "step_contracts"
REVIEWS_DIR = PIPELINE_ROOT / ".cache" / "step_reviews"
RUN_LOGS_DIR = PIPELINE_ROOT / ".cache" / "step_runs"


@dataclass
class StepResult:
    step: int
    status: str = ""
    duration_s: float = 0.0
    dod_checks: dict[str, bool] = field(default_factory=dict)
    dod_failures: list[str] = field(default_factory=list)
    dod_override_used: bool = False
    invariants_passed: bool | None = None
    regression_passed: bool | None = None
    checkpoint_tag: str = ""
    halt_reason: str = ""
    agent_signaled_complete: bool = False
    retry_of_rollback: bool = False
    run_log_path: str = ""
    recoverable_timeout: bool = False
    resume_hint: str = ""


def _is_ignored_search_path(path: Path) -> bool:
    ignored = {".git", ".cache", ".venv", "node_modules", "__pycache__"}
    return any(part in ignored for part in path.parts)


def _search_by_basename(name: str) -> list[Path]:
    matches: list[Path] = []
    for path in PIPELINE_ROOT.rglob(name):
        if not path.is_file():
            continue
        if _is_ignored_search_path(path):
            continue
        matches.append(path)
    return sorted(matches)


def _resolve_expected_file_path(raw_path: str) -> tuple[Path | None, str, list[str]]:
    """Resolve planned file references to concrete repository paths.

    Returns (resolved_path, status, candidates).
    status is one of: exact, alias, basename_unique, ambiguous, missing, skipped.
    """
    if raw_path.startswith("$") or "{" in raw_path:
        return None, "skipped", []

    candidate = PIPELINE_ROOT / raw_path
    if candidate.exists():
        return candidate, "exact", [str(candidate.relative_to(PIPELINE_ROOT))]

    basename_aliases = {
        "writer_prompt_contracts.yaml": PIPELINE_ROOT
        / "config"
        / "s0"
        / "writer_prompt_contracts.yaml",
        "convention_spec.json": PIPELINE_ROOT / ".cache" / "convention_spec.json",
        "data/core_docs.db": PIPELINE_ROOT
        / ".cache"
        / "sqlite_kb"
        / "current"
        / "core_docs.sqlite",
        "data/rust_reference.db": PIPELINE_ROOT
        / ".cache"
        / "sqlite_kb"
        / "current"
        / "rust_reference.sqlite",
        "data/fls_spec.db": PIPELINE_ROOT / ".cache" / "sqlite_kb" / "current" / "fls_spec.db",
    }
    alias = basename_aliases.get(raw_path)
    if alias is not None and alias.exists():
        return alias, "alias", [str(alias.relative_to(PIPELINE_ROOT))]

    basename = Path(raw_path).name
    matches = _search_by_basename(basename)
    rel_matches = [str(path.relative_to(PIPELINE_ROOT)) for path in matches]
    if len(matches) == 1:
        return matches[0], "basename_unique", rel_matches
    if len(matches) > 1:
        return None, "ambiguous", rel_matches
    return None, "missing", []


def _normalize_prerequisite_ref(step_n: int, path_ref: str) -> str:
    overrides = STEP_PREREQ_REF_OVERRIDES.get(step_n, {})
    return overrides.get(path_ref, path_ref)


def _is_optional_prerequisite(step_n: int, path_ref: str) -> bool:
    optional = STEP_OPTIONAL_PREREQS.get(step_n, set())
    return path_ref in optional


def _get_waiver_token(step_n: int, path_ref: str) -> str | None:
    return STEP_WAIVER_RULES.get(step_n, {}).get(path_ref)


def _has_active_waiver(token: str, path_ref: str) -> bool:
    deviations = PIPELINE_ROOT / "STEP_DEVIATIONS.md"
    if not deviations.exists():
        return False
    text = deviations.read_text(encoding="utf-8")
    return f"Waiver `{token}` is active" in text and f"file_exists:{path_ref}" in text


def _extract_prerequisite_paths(step_text: str) -> list[str]:
    section = re.search(r"## Prerequisites\s*\n(.*?)(?=\n## |\n---|\Z)", step_text, re.DOTALL)
    if not section:
        return []
    body = section.group(1)

    paths: list[str] = []
    for value in re.findall(r"`([^`]+)`", body):
        if re.search(r"\.(?:py|json|yaml|yml|db|rst|md)$", value):
            paths.append(value.strip())

    for value in re.findall(
        r"(?<![A-Za-z0-9_./-])(\.?[A-Za-z0-9_./-]+\.(?:py|json|yaml|yml|db|rst|md))",
        body,
    ):
        if value.startswith("http"):
            continue
        paths.append(value.strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path.startswith("./"):
            path = path[2:]
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def _run_semantic_backend_preflight_check() -> tuple[bool, str]:
    script = PIPELINE_ROOT / "scripts" / "sqlite_check_semantic_backend.py"
    if not script.exists():
        return False, "semantic backend check script missing"
    result = subprocess.run(
        ["uv", "run", "python", str(script)],
        cwd=PIPELINE_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    output = (result.stdout or result.stderr).strip()
    if not output:
        output = f"exit={result.returncode}"
    return result.returncode == 0, output[-400:]


def _write_run_log(step_n: int, session_id: str, exit_code: int, stdout: str, stderr: str) -> Path:
    RUN_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    path = RUN_LOGS_DIR / f"step_{step_n:02d}_{stamp}.json"
    payload = {
        "step": step_n,
        "session_id": session_id,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_step_file(step_n: int) -> str:
    path = PLAN_DIR / f"step_{step_n:02d}.md"
    if not path.exists():
        raise FileNotFoundError(f"step file not found: {path}")
    return path.read_text(encoding="utf-8")


def load_context_files() -> str:
    overview = (PLAN_DIR / "OVERVIEW.md").read_text(encoding="utf-8")
    autonomy = (PLAN_DIR / "AUTONOMY.md").read_text(encoding="utf-8")
    return f"# OVERVIEW\n\n{overview}\n\n# AUTONOMY\n\n{autonomy}"


def load_resume_state() -> str:
    checkpoint_script = PIPELINE_ROOT / "scripts" / "resume_checkpoint.py"
    if not checkpoint_script.exists():
        return "(No resume checkpoint available)"
    result = subprocess.run(
        ["python3", str(checkpoint_script)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return result.stdout


def load_prerequisite_contracts(step_n: int) -> str:
    deps = STEP_DEPS.get(step_n, [])
    if not deps:
        return "(No prerequisite contracts)"
    sections: list[str] = []
    for dep in sorted(deps):
        contract_path = CONTRACTS_DIR / f"step_{dep:02d}_contract.json"
        if contract_path.exists():
            data = contract_path.read_text(encoding="utf-8")
            sections.append(f"## Contract: Step {dep}\n```json\n{data}\n```")
        else:
            sections.append(f"## Contract: Step {dep}\n(not yet generated)")
    return "\n\n".join(sections)


def check_prerequisites(step_n: int) -> tuple[bool, list[str], list[dict[str, Any]]]:
    failures: list[str] = []
    details: list[dict[str, Any]] = []

    for dep in STEP_DEPS.get(step_n, []):
        tag = f"step-{dep:02d}-complete"
        result = subprocess.run(
            ["git", "tag", "-l", tag], capture_output=True, text=True, check=False
        )
        if tag not in result.stdout:
            failures.append(f"missing git tag: {tag}")
            details.append({"kind": "tag", "tag": tag, "status": "missing"})
        else:
            details.append({"kind": "tag", "tag": tag, "status": "ok"})

    step_text = load_step_file(step_n)
    for path_ref in _extract_prerequisite_paths(step_text):
        effective_ref = _normalize_prerequisite_ref(step_n, path_ref)
        resolved, status, candidates = _resolve_expected_file_path(effective_ref)
        if status in {"exact", "alias", "basename_unique", "skipped"}:
            details.append(
                {
                    "kind": "file",
                    "ref": path_ref,
                    "status": "ok" if status != "skipped" else "skipped",
                    "resolution": status,
                    "resolved": (
                        str(resolved.relative_to(PIPELINE_ROOT))
                        if resolved is not None and resolved.exists()
                        else None
                    ),
                }
            )
            continue

        waiver_token = _get_waiver_token(step_n, effective_ref)
        if waiver_token and _has_active_waiver(waiver_token, effective_ref):
            details.append(
                {
                    "kind": "file",
                    "ref": path_ref,
                    "status": "waived",
                    "resolution": status,
                    "waiver": waiver_token,
                }
            )
            continue

        if _is_optional_prerequisite(step_n, effective_ref):
            details.append(
                {
                    "kind": "file",
                    "ref": path_ref,
                    "status": "optional",
                    "resolution": status,
                    "note": "missing optional prerequisite; default behavior applies",
                }
            )
            continue

        if status == "ambiguous":
            failures.append(
                f"prerequisite file ambiguous: {path_ref} -> {', '.join(candidates[:5])}"
            )
        else:
            failures.append(f"prerequisite file missing: {path_ref}")
        details.append(
            {
                "kind": "file",
                "ref": path_ref,
                "status": "fail",
                "resolution": status,
                "candidates": candidates,
            }
        )

    if step_n == 9:
        backend_ok, backend_message = _run_semantic_backend_preflight_check()
        details.append(
            {
                "kind": "check",
                "name": "semantic_backend",
                "status": "ok" if backend_ok else "fail",
                "message": backend_message,
            }
        )
        if not backend_ok:
            failures.append(f"semantic backend unhealthy: {backend_message}")

    return len(failures) == 0, failures, details


def compose_prompt(step_n: int) -> str:
    return f"""{load_context_files()}

# CURRENT STEP: Step {step_n}

{load_step_file(step_n)}

# INTEGRATION CONTRACTS FROM PREREQUISITE STEPS

{load_prerequisite_contracts(step_n)}

# RESUME STATE FROM PRIOR STEPS

{load_resume_state()}

# INSTRUCTIONS

You are executing Step {step_n} of the v17.2 pipeline rearchitecture plan.

1. Read the step file carefully.
2. Read prerequisite contracts and honor known deviations.
3. Verify assumptions listed by the step.
4. Implement the step.
5. Before finishing, write STEP_DEVIATIONS.md in pipeline root.
6. Verify every Definition of Done item.
7. When done, print exactly: STEP_{step_n}_COMPLETE

Do not proceed to the next step. Stop after this step.
"""


def run_opencode_session(step_n: int, prompt: str) -> tuple[int, str, str, str, Path | None]:
    requested_session = f"step-{step_n:02d}-{int(time.time())}"
    timeout = STEP_TIMEOUTS.get(step_n, 3600)
    try:
        # Passing --session with a non-existent ID on attached runs can yield a
        # successful exit with empty stdout/stderr. For fresh step execution we
        # omit --session and let OpenCode allocate a session server-side.
        result = subprocess.run(
            [
                "opencode",
                "run",
                "--attach",
                OPENCODE_SERVER,
                "--format",
                "json",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0 and not result.stdout.strip():
            result = subprocess.CompletedProcess(
                args=result.args,
                returncode=86,
                stdout=result.stdout,
                stderr="opencode run returned success but emitted no output",
            )

        log_path = _write_run_log(
            step_n,
            requested_session,
            result.returncode,
            result.stdout,
            result.stderr,
        )
        return (
            result.returncode,
            result.stdout,
            result.stderr,
            requested_session,
            log_path,
        )
    except subprocess.TimeoutExpired:
        log_path = _write_run_log(
            step_n,
            requested_session,
            -1,
            "",
            f"TIMEOUT after {timeout}s",
        )
        return -1, "", f"TIMEOUT after {timeout}s", requested_session, log_path


def validate_dod(step_n: int) -> dict[str, bool]:
    step_text = load_step_file(step_n)
    results: dict[str, bool] = {}
    files_section = re.search(
        r"## Files Modified\s*\n(.*?)(?=\n## |\n---|\Z)", step_text, re.DOTALL
    )
    if files_section:
        for match in re.finditer(
            r"\|\s*(?:NEW:\s*)?`?([^|`]+\.(?:py|json|yaml|yml|db|rst|md))`?\s*\|",
            files_section.group(1),
        ):
            filepath = match.group(1).strip()
            if filepath.startswith("$") or "{" in filepath:
                continue
            resolved, status, _candidates = _resolve_expected_file_path(filepath)
            results[f"file_exists:{filepath}"] = status in {
                "exact",
                "alias",
                "basename_unique",
            } and (resolved is not None and resolved.exists())

    test_result = subprocess.run(
        ["uv", "run", "pytest", "tests/", "-x", "--tb=line", "-q", "--timeout=60"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    results["tests_pass"] = test_result.returncode == 0
    return results


def run_invariants() -> bool:
    result = subprocess.run(
        ["uv", "run", "pytest", "tests/test_v3_invariants.py", "-x", "--tb=short"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        print(f"  invariants output:\n{result.stdout[-800:]}")
    return result.returncode == 0


def run_regression_smoke() -> tuple[bool, str]:
    reports_dir = PIPELINE_ROOT / ".cache" / "sqlite_kb" / "reports"
    if not reports_dir.exists():
        return True, "no reports dir; skip"
    report_dirs = sorted(reports_dir.iterdir(), key=lambda path: path.stat().st_mtime)
    if not report_dirs:
        return True, "no reports; skip"
    latest = report_dirs[-1]
    issues: list[str] = []
    for gate_file, gate_name in [
        ("evidence_synthesizer_gate_report.json", "evidence_gate"),
        ("citation_resolution_report.json", "citation_resolution"),
    ]:
        path = latest / gate_file
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        status = str(data.get("status", "unknown"))
        if status != "pass":
            issues.append(f"{gate_name}={status}")
    if issues:
        return False, f"REGRESSION: {', '.join(issues)}"
    return True, "all gates pass"


def _discover_checkpoint_run_dir() -> Path | None:
    reports_root = PIPELINE_ROOT / ".cache" / "sqlite_kb" / "reports"
    if not reports_root.exists():
        return None

    candidates: list[Path] = []
    for run_dir in reports_root.iterdir():
        if not run_dir.is_dir():
            continue
        rerender_dir = run_dir / "rerendered_rst"
        if not rerender_dir.exists():
            continue
        if list(rerender_dir.glob("*.rst")):
            candidates.append(run_dir)

    if candidates:
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return candidates[0]
    return None


def _discover_step5_resume_candidate() -> Path | None:
    reports_root = PIPELINE_ROOT / ".cache" / "sqlite_kb" / "reports"
    if not reports_root.exists():
        return None
    candidates: list[Path] = []
    for run_dir in reports_root.glob("target_expansion_v17*"):
        if not run_dir.is_dir():
            continue
        if not (run_dir / "targets.json").exists():
            continue
        if not (run_dir / "calibration_target_rationale.json").exists():
            continue
        if (
            not (run_dir / "core_docs_eval_report.json").exists()
            and not (run_dir / "rust_reference_eval_report.json").exists()
        ):
            continue
        candidates.append(run_dir)
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def run_integration_checkpoint(checkpoint: str, run_dir: Path | None = None) -> tuple[bool, str]:
    script = PIPELINE_ROOT / "scripts" / "integration_checkpoint.py"
    if not script.exists():
        return True, f"integration checkpoint script missing; skip {checkpoint}"

    command = ["python3", str(script), "--checkpoint", checkpoint]
    if run_dir is not None:
        command.extend(["--run-dir", str(run_dir)])

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    return result.returncode == 0, result.stdout[-600:]


def run_prerequisite_preflight(step_n: int) -> tuple[bool, str]:
    ok, failures, details = check_prerequisites(step_n)
    lines = [f"Preflight Step {step_n}", ""]
    for detail in details:
        if detail.get("kind") == "tag":
            lines.append(f"- [TAG:{detail['status'].upper()}] {detail['tag']}")
            continue
        if detail.get("kind") == "check":
            name = detail.get("name", "check")
            status = str(detail.get("status", "unknown")).upper()
            message = detail.get("message", "")
            lines.append(f"- [CHECK:{status}] {name}: {message}")
            continue
        ref = detail.get("ref", "")
        status = str(detail.get("status", "unknown")).upper()
        resolution = detail.get("resolution", "")
        resolved = detail.get("resolved")
        if resolved:
            lines.append(f"- [FILE:{status}] {ref} -> {resolved} ({resolution})")
        elif detail.get("candidates"):
            candidates = ", ".join(detail["candidates"][:5])
            lines.append(f"- [FILE:{status}] {ref} -> candidates: {candidates}")
        else:
            lines.append(f"- [FILE:{status}] {ref} ({resolution})")
    if failures:
        lines.extend(["", "Failures:"])
        lines.extend([f"- {failure}" for failure in failures])
    return ok, "\n".join(lines)


def git_checkpoint(step_n: int) -> str:
    tag = f"step-{step_n:02d}-complete"
    subprocess.run(["git", "add", "-A"], cwd=PIPELINE_ROOT, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"step-{step_n:02d}: complete", "--allow-empty"],
        cwd=PIPELINE_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    subprocess.run(
        ["git", "tag", "-d", tag], cwd=PIPELINE_ROOT, capture_output=True, text=True, check=False
    )
    subprocess.run(["git", "tag", tag], cwd=PIPELINE_ROOT, check=True)
    return tag


def attempt_rollback(step_n: int) -> bool:
    deps = STEP_DEPS.get(step_n, [])
    if not deps:
        return False
    prev_tag = f"step-{max(deps):02d}-complete"
    tag_result = subprocess.run(
        ["git", "tag", "-l", prev_tag], capture_output=True, text=True, check=False
    )
    if prev_tag not in tag_result.stdout:
        print(f"  cannot rollback; missing tag {prev_tag}")
        return False
    subprocess.run(
        ["git", "diff", "--stat", prev_tag, "HEAD"],
        cwd=PIPELINE_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if os.environ.get("ORCHESTRATOR_ALLOW_HARD_ROLLBACK", "0") != "1":
        print("  hard rollback disabled (set ORCHESTRATOR_ALLOW_HARD_ROLLBACK=1 to enable)")
        return False

    safety_tag = f"pre-rollback-step-{step_n:02d}-{int(time.time())}"
    subprocess.run(["git", "tag", safety_tag, "HEAD"], cwd=PIPELINE_ROOT, check=False)
    result = subprocess.run(
        ["git", "reset", "--hard", prev_tag],
        cwd=PIPELINE_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        print(f"  rollback complete (safety tag: {safety_tag})")
    return result.returncode == 0


def generate_contract(step_n: int) -> Path:
    script = PIPELINE_ROOT / "scripts" / "generate_contract.py"
    if not script.exists():
        print("  [WARN] generate_contract.py not found")
        return Path("/dev/null")
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["python3", str(script), "--step", str(step_n)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    path = CONTRACTS_DIR / f"step_{step_n:02d}_contract.json"
    if result.returncode == 0:
        print(f"  [OK] Contract: {path}")
    else:
        print(f"  [WARN] Contract generation failed: {result.stderr[:200]}")
    return path


def generate_step_review(step_n: int, result: StepResult) -> Path:
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    review_path = REVIEWS_DIR / f"step_{step_n:02d}_review.md"

    lines: list[str] = [
        f"# Step {step_n} - Review File",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        f"**Status:** {result.status}",
    ]
    if result.halt_reason:
        lines.append(f"**Halt Reason:** {result.halt_reason}")
    if result.resume_hint:
        lines.append(f"**Resume Hint:** {result.resume_hint}")
    if result.run_log_path:
        lines.append(f"**Run Log:** `{result.run_log_path}`")
    lines.extend([f"**Duration:** {result.duration_s:.0f}s", "", "## Machine Check Results", ""])

    for check, passed in result.dod_checks.items():
        icon = "[OK]" if passed else "[FAIL]"
        lines.append(f"- {icon} {check}")
    if result.dod_failures:
        gate_icon = "[WARN]" if result.dod_override_used else "[FAIL]"
        gate_state = "override active" if result.dod_override_used else "blocking"
        lines.append(
            f"- {gate_icon} DoD Gate: {gate_state}; failed checks: {', '.join(result.dod_failures)}"
        )
    elif result.dod_checks:
        lines.append("- [OK] DoD Gate: all checks passed")
    lines.append("")
    inv_icon = (
        "[SKIP]"
        if result.invariants_passed is None
        else ("[OK]" if result.invariants_passed else "[FAIL]")
    )
    reg_icon = (
        "[SKIP]"
        if result.regression_passed is None
        else ("[OK]" if result.regression_passed else "[FAIL]")
    )
    lines.append(f"- {inv_icon} Invariants")
    lines.append(f"- {reg_icon} Regression")
    lines.append("")

    contract_path = CONTRACTS_DIR / f"step_{step_n:02d}_contract.json"
    if contract_path.exists():
        lines.extend(["## Integration Contract Summary", ""])
        try:
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            deviations = contract.get("deviations", [])
            known_issues = contract.get("known_issues", [])
            if deviations:
                lines.append("### Deviations")
                lines.extend([f"- {item}" for item in deviations])
            if known_issues:
                lines.append("### Known Issues")
                lines.extend([f"- {item}" for item in known_issues])
            if not deviations and not known_issues:
                lines.append("No deviations or known issues reported.")
        except (json.JSONDecodeError, OSError):
            lines.append("Contract exists but could not be parsed.")
        lines.append("")

    lines.extend(["## Files Changed", ""])
    if result.checkpoint_tag:
        deps = STEP_DEPS.get(step_n, [])
        prev_tag = f"step-{max(deps):02d}-complete" if deps else "HEAD~1"
        diff = subprocess.run(
            ["git", "diff", "--stat", prev_tag, result.checkpoint_tag],
            cwd=PIPELINE_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if diff.stdout.strip():
            lines.extend(["```", diff.stdout.strip(), "```", ""])

    step_text = load_step_file(step_n)
    hrg_match = re.search(r"## Human Review Gate\s*\n(.*?)(?=\n## |\Z)", step_text, re.DOTALL)
    if hrg_match:
        lines.extend(["## Human Review Gate", "", hrg_match.group(1).strip(), ""])

    lines.extend(["---", ""])
    if result.status == "pass":
        next_step = step_n + 1
        if next_step <= max(STEPS):
            lines.append(
                f"**To proceed:** `python scripts/step_orchestrator.py --start-from {next_step}`"
            )
        else:
            lines.append("**All steps complete.**")
    else:
        if result.recoverable_timeout and result.resume_hint:
            lines.append(f"**Step paused (recoverable).** {result.resume_hint}")
        else:
            lines.append(
                f"**Step failed.** Re-run: `python scripts/step_orchestrator.py --step {step_n}`"
            )

    review_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  review file: {review_path}")
    return review_path


def execute_step(step_n: int, is_retry: bool = False) -> StepResult:
    result = StepResult(step=step_n, retry_of_rollback=is_retry)
    start = time.monotonic()

    print("  checking prerequisites...")
    ok, failures, _details = check_prerequisites(step_n)
    if not ok:
        result.status = "halt"
        result.halt_reason = f"prerequisites failed: {failures}"
        result.duration_s = time.monotonic() - start
        return result

    print("  composing prompt and launching OpenCode session...")
    prompt = compose_prompt(step_n)
    exit_code, stdout, stderr, _session_id, run_log_path = run_opencode_session(step_n, prompt)
    if run_log_path is not None:
        result.run_log_path = str(run_log_path)
    if exit_code == -1:
        result.status = "halt"
        result.halt_reason = f"STEP_TIMEOUT: {stderr}; see {result.run_log_path}"
        if step_n == 5:
            resume_dir = _discover_step5_resume_candidate()
            if resume_dir is not None:
                result.recoverable_timeout = True
                result.resume_hint = (
                    "Resume candidate detected at "
                    f"`{resume_dir.relative_to(PIPELINE_ROOT)}`. "
                    "Re-run Step 5 and reuse this run-id if prompted."
                )
        result.duration_s = time.monotonic() - start
        return result
    if exit_code != 0:
        result.status = "halt"
        result.halt_reason = (
            f"opencode run failed with exit_code={exit_code}; see {result.run_log_path}"
        )
        result.duration_s = time.monotonic() - start
        return result

    result.agent_signaled_complete = f"STEP_{step_n}_COMPLETE" in stdout
    if not result.agent_signaled_complete:
        result.status = "halt"
        result.halt_reason = (
            f"agent did not signal STEP_{step_n}_COMPLETE; see {result.run_log_path}"
        )

    print("  validating Definition of Done...")
    result.dod_checks = validate_dod(step_n)
    failed_dod = [check for check, passed in result.dod_checks.items() if not passed]
    allow_dod_failure = os.environ.get("ORCHESTRATOR_ALLOW_DOD_FAILURE", "0") == "1"
    result.dod_failures = failed_dod
    result.dod_override_used = bool(failed_dod) and allow_dod_failure
    if failed_dod and not allow_dod_failure:
        result.status = "halt"
        result.halt_reason = f"DOD_FAILED: {', '.join(failed_dod)}"
        result.duration_s = time.monotonic() - start
        return result
    if failed_dod and allow_dod_failure:
        print(
            "  [WARN] DoD override enabled via ORCHESTRATOR_ALLOW_DOD_FAILURE=1; "
            f"continuing with failed checks: {', '.join(failed_dod)}"
        )
    if not result.agent_signaled_complete:
        result.duration_s = time.monotonic() - start
        return result

    print("  running invariants suite...")
    result.invariants_passed = run_invariants()

    print("  running regression smoke...")
    result.regression_passed, detail = run_regression_smoke()
    if not result.regression_passed:
        print(f"  [FAIL] {detail}")

    result.duration_s = time.monotonic() - start
    return result


def orchestrate(
    start_from: int = 2,
    single_step: int | None = None,
    dry_run: bool = False,
    preflight: bool = False,
    checkpoint_run_dir: Path | None = None,
) -> int:
    step_n = single_step if single_step is not None else start_from
    if step_n not in STEPS:
        print(f"ERROR: step {step_n} out of range {min(STEPS)}-{max(STEPS)}")
        return 2

    if preflight:
        ok, report = run_prerequisite_preflight(step_n)
        print(report)
        return 0 if ok else 1

    if dry_run:
        targets = [step_n] if single_step is not None else [s for s in STEPS if s >= step_n]
        print("DRY RUN: checking prerequisites\n")
        all_ok = True
        for s in targets:
            ok, failures, details = check_prerequisites(s)
            print(f"  {'[OK]' if ok else '[FAIL]'} Step {s}")
            for detail in details:
                if detail.get("kind") == "file" and detail.get("status") == "ok":
                    resolved = detail.get("resolved")
                    if resolved:
                        print(f"    - resolved {detail['ref']} -> {resolved}")
            for failure in failures:
                print(f"    - {failure}")
            all_ok = all_ok and ok
        return 0 if all_ok else 1

    print("=" * 60)
    print(f"STEP {step_n}")
    print("=" * 60)

    result = execute_step(step_n)

    if result.status == "halt":
        generate_step_review(step_n, result)
        return 1

    if result.invariants_passed is False or result.regression_passed is False:
        reason = "INVARIANT_REGRESSION" if not result.invariants_passed else "REGRESSION_DETECTED"
        print(f"\n  {reason}; attempting rollback")
        if attempt_rollback(step_n):
            print("  rollback succeeded; retrying once")
            result = execute_step(step_n, is_retry=True)
            if result.invariants_passed is False or result.regression_passed is False:
                result.status = "halt"
                result.halt_reason = f"{reason} persists after rollback+retry"
                generate_step_review(step_n, result)
                return 1
        else:
            result.status = "halt"
            result.halt_reason = (
                f"{reason}; rollback unavailable or failed "
                f"(set ORCHESTRATOR_ALLOW_HARD_ROLLBACK=1 to enable hard reset)"
            )
            generate_step_review(step_n, result)
            return 1

    resolved_checkpoint_run_dir = checkpoint_run_dir or _discover_checkpoint_run_dir()

    if step_n == CP_A_AFTER_STEP:
        cp_ok, cp_detail = run_integration_checkpoint("A", resolved_checkpoint_run_dir)
        if not cp_ok:
            result.status = "halt"
            result.halt_reason = f"CP-A failed (run_dir={resolved_checkpoint_run_dir}): {cp_detail}"
            generate_step_review(step_n, result)
            return 1
    elif step_n == CP_B_AFTER_STEP:
        cp_ok, cp_detail = run_integration_checkpoint("B", resolved_checkpoint_run_dir)
        if not cp_ok:
            result.status = "halt"
            result.halt_reason = f"CP-B failed (run_dir={resolved_checkpoint_run_dir}): {cp_detail}"
            generate_step_review(step_n, result)
            return 1
    elif step_n == CP_C_AFTER_STEP:
        cp_ok, cp_detail = run_integration_checkpoint("B", resolved_checkpoint_run_dir)
        if not cp_ok:
            result.status = "halt"
            result.halt_reason = (
                f"CP-C failed (run_dir={resolved_checkpoint_run_dir}, using checkpoint B checks): "
                f"{cp_detail}"
            )
            generate_step_review(step_n, result)
            return 1

    print("  creating git checkpoint...")
    result.checkpoint_tag = git_checkpoint(step_n)
    print(f"  tagged: {result.checkpoint_tag}")

    result.status = "pass"
    generate_contract(step_n)
    review_path = generate_step_review(step_n, result)

    print(f"\n  [OK] Step {step_n} complete ({result.duration_s:.0f}s)")
    print("  HUMAN REVIEW REQUIRED")
    print(f"  Read: {review_path}")
    if step_n < max(STEPS):
        print(f"  Then: python scripts/step_orchestrator.py --start-from {step_n + 1}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Step-level orchestrator for v17.2 plan")
    parser.add_argument("--start-from", type=int, default=2, help="Run this step number")
    parser.add_argument("--step", type=int, default=None, help="Run this specific step number")
    parser.add_argument("--dry-run", action="store_true", help="Validate prerequisites only")
    parser.add_argument(
        "--preflight", action="store_true", help="Detailed prerequisite diagnostics"
    )
    parser.add_argument(
        "--checkpoint-run-dir",
        type=Path,
        default=None,
        help="Explicit report run directory for CP-A/CP-B checks",
    )
    args = parser.parse_args()
    exit_code = orchestrate(
        start_from=args.start_from,
        single_step=args.step,
        dry_run=args.dry_run,
        preflight=args.preflight,
        checkpoint_run_dir=args.checkpoint_run_dir,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
