"""Compare pipeline output against extraction baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BASELINE_DIR = Path(".cache/extraction_baseline/outputs")
NON_DETERMINISTIC_KEYS = {"run_id", "recorded_at", "generated_at", "timestamp", "date"}


def canonicalize(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            key: "__NONDETERMINISTIC__" if key in NON_DETERMINISTIC_KEYS else canonicalize(value)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [canonicalize(item) for item in data]
    return data


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[Any]:
    rows: list[Any] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if raw:
            rows.append(json.loads(raw))
    return rows


def _compare_pair(baseline: Path, current: Path) -> list[str]:
    if baseline.suffix == ".jsonl":
        left = canonicalize(_load_jsonl(baseline))
        right = canonicalize(_load_jsonl(current))
    else:
        left = canonicalize(_load_json(baseline))
        right = canonicalize(_load_json(current))
    if left == right:
        return []
    left_str = json.dumps(left, sort_keys=True, indent=2)
    right_str = json.dumps(right, sort_keys=True, indent=2)
    diffs: list[str] = []
    for idx, (l_line, r_line) in enumerate(
        zip(left_str.splitlines(), right_str.splitlines()), start=1
    ):
        if l_line != r_line:
            diffs.append(f"line {idx}: baseline={l_line}")
            diffs.append(f"line {idx}: current ={r_line}")
        if len(diffs) >= 20:
            diffs.append("... (truncated)")
            break
    return diffs or ["payload length differs"]


def _compare_rst_pair(baseline: Path, current: Path) -> list[str]:
    baseline_text = baseline.read_text(encoding="utf-8")
    current_text = current.read_text(encoding="utf-8")
    if baseline_text == current_text:
        return []

    left_lines = baseline_text.splitlines()
    right_lines = current_text.splitlines()
    diffs: list[str] = []
    for idx, (l_line, r_line) in enumerate(zip(left_lines, right_lines), start=1):
        if l_line != r_line:
            diffs.append(f"line {idx}: baseline={l_line}")
            diffs.append(f"line {idx}: current ={r_line}")
        if len(diffs) >= 20:
            diffs.append("... (truncated)")
            break
    if not diffs and len(left_lines) != len(right_lines):
        diffs.append(f"line_count differs: baseline={len(left_lines)} current={len(right_lines)}")
    return diffs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    current_dir = Path(".cache/sqlite_kb/reports") / args.run_id
    if not current_dir.exists() or not BASELINE_DIR.exists():
        print("baseline/current directories missing")
        return 1

    ok = True
    baseline_files = (
        sorted(BASELINE_DIR.rglob("*.json"))
        + sorted(BASELINE_DIR.rglob("*.jsonl"))
        + sorted(BASELINE_DIR.rglob("*.rst"))
    )
    for base_file in baseline_files:
        rel = base_file.relative_to(BASELINE_DIR)
        curr_file = current_dir / rel
        if not curr_file.exists():
            print(f"MISSING {rel}")
            ok = False
            continue
        if base_file.suffix == ".rst":
            diffs = _compare_rst_pair(base_file, curr_file)
        else:
            diffs = _compare_pair(base_file, curr_file)
        if diffs:
            print(f"DRIFT {rel}")
            for row in diffs:
                print(f"  {row}")
            ok = False
        else:
            print(f"OK {rel}")

    if ok:
        print("Behavioral snapshot: NO DRIFT detected")
        return 0
    print("EXTRACTION_BEHAVIORAL_CHANGE detected")
    return 1


if __name__ == "__main__":
    sys.exit(main())
