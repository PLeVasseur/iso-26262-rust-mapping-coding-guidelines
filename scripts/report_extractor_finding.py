#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import sys

from _common import EXIT_RUNTIME_FAIL, EXIT_SUCCESS, read_yaml, repo_root, utc_now, write_yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append a normalized extractor finding entry")
    parser.add_argument("--finding-id", help="Optional finding id; generated when omitted")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--due-by", required=True)
    parser.add_argument("--run-id-before", required=True)
    parser.add_argument("--run-id-after", required=True)
    parser.add_argument("--tool-version", required=True)
    parser.add_argument("--tool-commit-sha", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--severity", choices=["S0", "S1", "S2", "S3"], required=True)
    parser.add_argument(
        "--status",
        choices=["new", "triaged", "upstream-open", "fixed-upstream", "verified"],
        default="new",
    )
    parser.add_argument("--expected", required=True)
    parser.add_argument("--observed", required=True)
    parser.add_argument("--repro-cmd", required=True)
    parser.add_argument("--upstream-issue-url", default="")
    return parser.parse_args()


def generate_finding_id(payload_seed: str) -> str:
    digest = hashlib.sha1(payload_seed.encode("utf-8")).hexdigest()[:10].upper()
    return f"FIND-{digest}"


def main() -> int:
    args = parse_args()
    root = repo_root()
    findings_path = root / "feedback" / "extractor_findings.yaml"

    payload = read_yaml(findings_path) if findings_path.exists() else {"version": 1, "findings": []}
    findings = payload.setdefault("findings", [])

    seed = "|".join(
        [
            args.run_id_before,
            args.run_id_after,
            args.stage,
            args.severity,
            args.observed,
        ]
    )
    finding_id = args.finding_id or generate_finding_id(seed)

    if any(entry.get("finding_id") == finding_id for entry in findings):
        print(f"[findings][error] finding_id already exists: {finding_id}")
        return EXIT_RUNTIME_FAIL

    now = utc_now()
    entry = {
        "finding_id": finding_id,
        "date": now.split("T")[0],
        "opened_at": now,
        "owner": args.owner,
        "due_by": args.due_by,
        "run_id_before": args.run_id_before,
        "run_id_after": args.run_id_after,
        "tool_version": args.tool_version,
        "tool_commit_sha": args.tool_commit_sha,
        "stage": args.stage,
        "severity": args.severity,
        "status": args.status,
        "expected": args.expected,
        "observed": args.observed,
        "repro_cmd": args.repro_cmd,
        "upstream_issue_url": args.upstream_issue_url,
    }
    findings.append(entry)

    write_yaml(findings_path, payload)
    print(f"[findings] appended {finding_id} -> {findings_path.relative_to(root)}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
