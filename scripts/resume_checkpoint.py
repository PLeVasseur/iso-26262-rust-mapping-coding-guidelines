"""Resume checkpoint utility.

Run after any pause to re-establish state across git, environment,
upstream pinning, latest reports, and generated step contracts.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
from pathlib import Path


def run_cmd(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _guidelines_repo() -> str:
    return os.environ.get(
        "GUIDELINES_REPO", "/Users/pete.levasseur/personal/safety-critical-rust-coding-guidelines"
    )


def main() -> None:
    guidelines_repo = _guidelines_repo()
    print("=== Resume Checkpoint ===\n")

    print("1. Code repository state:")
    result = run_cmd(["git", "log", "--oneline", "-5"])
    print(result.stdout)

    result = run_cmd(["git", "tag", "-l", "step-*-complete", "--sort=-creatordate"])
    if result.stdout.strip():
        print("  Step checkpoints:")
        for tag in result.stdout.strip().split("\n")[:5]:
            print(f"    {tag}")
    else:
        print("  No step checkpoints yet")

    print("\n2. Guidelines repo pin:")
    result = run_cmd(["python3", "scripts/verify_upstream_pin.py", guidelines_repo])
    print(result.stdout)
    if result.returncode != 0:
        print("  [WARN] Upstream pin mismatch; investigate before proceeding")

    print("3. Environment:")
    env = os.environ.copy()
    env["GUIDELINES_REPO"] = guidelines_repo
    result = run_cmd(["python3", "scripts/validate_environment.py"], env=env)
    print(result.stdout)

    spec_path = Path(".cache/convention_spec.json")
    if spec_path.exists():
        print("4. Convention spec:")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        commit = spec.get("exemplar_source_commit", "unknown")
        print(f"  exemplar_source_commit: {commit}")

        result = run_cmd(["git", "rev-parse", "HEAD"], cwd=guidelines_repo)
        repo_head = result.stdout.strip()
        if commit == repo_head:
            print("  [OK] matches guidelines repo HEAD")
        else:
            print(f"  [WARN] stale convention spec; repo HEAD is {repo_head[:12]}")
    else:
        print("4. Convention spec: not yet created (expected before Step 7)")

    print("\n5. Latest reports:")
    reports = sorted(glob.glob(".cache/sqlite_kb/reports/*/"))
    if reports:
        latest = reports[-1]
        print(f"  Latest run: {latest}")
        for report in [
            "go_no_go_decision.json",
            "output_conformance_report.json",
            "code_validation_report.json",
        ]:
            path = Path(latest) / report
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                status = data.get("status", data.get("go_no_go_decision", "?"))
                print(f"  {report}: {status}")
    else:
        print("  No reports found")

    print("\n6. Integration contracts:")
    contracts_dir = Path(".cache/step_contracts")
    if contracts_dir.exists():
        contracts = sorted(contracts_dir.glob("step_*_contract.json"))
        for contract in contracts:
            data = json.loads(contract.read_text(encoding="utf-8"))
            deviations = len(data.get("deviations", []))
            known_issues = len(data.get("known_issues", []))
            print(f"  {contract.name}: {deviations} deviations, {known_issues} known issues")
    else:
        print("  No contracts yet")

    print("\n=== Resume checkpoint complete ===")


if __name__ == "__main__":
    main()
