"""Run standalone construct scope cardinality checks from persisted artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

if __package__ in {None, ""}:  # pragma: no cover - direct script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from validation.scope import check_scope_cardinality


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing required JSONL file: {path}")
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if raw:
            rows.append(json.loads(raw))
    return rows


def _index_by_draft_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        draft_id = str(row.get("draft_id", "")).strip()
        if draft_id:
            index[draft_id] = row
    return index


def _load_policy(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None


def run_scope_check(run_dir: Path, policy_path: Path | None = None) -> dict[str, Any]:
    drafts = _load_jsonl(run_dir / "drafts.jsonl")
    evidence = _index_by_draft_id(
        _load_jsonl(run_dir / "writer_subagent_outputs" / "evidence_synthesizer.jsonl")
    )
    policy = _load_policy(policy_path)

    scope_results: list[dict[str, Any]] = []
    scope_blocked_count = 0

    for draft in drafts:
        if str(draft.get("status", "")).strip().lower() == "abstain":
            continue
        draft_id = str(draft.get("draft_id", "")).strip()
        prompt_id = str(draft.get("target_prompt_id", draft.get("prompt_id", ""))).strip()
        ev = evidence.get(draft_id, {})

        raw_terms = ev.get("construct_terms", draft.get("construct_terms", []))
        construct_terms = [str(item).strip() for item in raw_terms if str(item).strip()]

        passed, result = check_scope_cardinality(construct_terms, prompt_id, config=policy)
        result["draft_id"] = draft_id
        result["target_id"] = str(draft.get("target_id", "")).strip()
        scope_results.append(result)
        if not passed:
            scope_blocked_count += 1

    report = {
        "source": "standalone",
        "results": scope_results,
        "total_checked": len(scope_results),
        "blocked_count": scope_blocked_count,
        "pass_rate": (len(scope_results) - scope_blocked_count) / max(1, len(scope_results)),
    }
    (run_dir / "scope_cardinality_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run standalone scope cardinality checks")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("config/s0/scope_gate_policy.yaml"),
        help="Path to scope gate policy YAML",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = run_scope_check(
        run_dir=args.run_dir.expanduser().resolve(),
        policy_path=args.policy.expanduser().resolve() if args.policy else None,
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "total_checked": report["total_checked"],
                "blocked_count": report["blocked_count"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
