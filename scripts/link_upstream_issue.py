#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys

from _common import EXIT_RUNTIME_FAIL, EXIT_SUCCESS, read_yaml, repo_root, run_command, write_yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create/link upstream extractor issue")
    parser.add_argument("--finding-id", required=True)
    parser.add_argument("--repo", required=True, help="GitHub repo in owner/name format")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = repo_root()
    findings_path = root / "feedback" / "extractor_findings.yaml"
    payload = read_yaml(findings_path)
    findings = payload.get("findings", [])

    finding = next(
        (entry for entry in findings if entry.get("finding_id") == args.finding_id), None
    )
    if finding is None:
        print(f"[upstream-link][error] finding_id not found: {args.finding_id}")
        return EXIT_RUNTIME_FAIL

    if not shutil.which("gh"):
        print("[upstream-link][error] gh CLI not found")
        return EXIT_RUNTIME_FAIL

    title = f"[{finding['severity']}] {finding['stage']} regression: {finding['finding_id']}"
    body = "\n".join(
        [
            "## Summary",
            f"- Finding ID: {finding['finding_id']}",
            f"- Severity: {finding['severity']}",
            f"- Stage: {finding['stage']}",
            f"- Run before: {finding['run_id_before']}",
            f"- Run after: {finding['run_id_after']}",
            f"- Tool version: {finding['tool_version']} ({finding['tool_commit_sha']})",
            "",
            "## Expected",
            finding["expected"],
            "",
            "## Observed",
            finding["observed"],
            "",
            "## Reproduction",
            f"`{finding['repro_cmd']}`",
        ]
    )

    result = run_command(
        ["gh", "issue", "create", "--repo", args.repo, "--title", title, "--body", body],
        cwd=root,
    )
    if result.returncode != 0:
        print("[upstream-link][error] failed to create issue")
        print(result.stderr)
        return EXIT_RUNTIME_FAIL

    issue_url = result.stdout.strip().splitlines()[-1].strip()
    finding["upstream_issue_url"] = issue_url
    if finding.get("status") in {"new", "triaged"}:
        finding["status"] = "upstream-open"
    write_yaml(findings_path, payload)

    print(f"[upstream-link] linked {args.finding_id} -> {issue_url}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
