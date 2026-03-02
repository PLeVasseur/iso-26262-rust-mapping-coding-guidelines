"""Validate FLS matching accuracy against exemplar ground truth."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    from context.exemplars import EXEMPLAR_MANIFEST
    from context.fls_lookup import resolve_fls_for_construct
except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(PROJECT_ROOT))
    from context.exemplars import EXEMPLAR_MANIFEST
    from context.fls_lookup import resolve_fls_for_construct

GUIDELINES_REPO = Path(
    os.environ.get(
        "GUIDELINES_REPO", "/Users/pete.levasseur/personal/safety-critical-rust-coding-guidelines"
    )
)


def extract_fls_ids_from_rst(rst_path: Path) -> list[str]:
    """Extract all :fls: directive values from an exemplar RST file."""
    content = rst_path.read_text(encoding="utf-8")
    matches = re.findall(r":fls:`(fls_\w+)`|:fls:\s+(fls_\w+)", content)
    return _normalize_fls_ids(matches)


def _normalize_fls_ids(matches: list[tuple[str, str]] | list[str]) -> list[str]:
    out: list[str] = []
    for match in matches:
        if isinstance(match, tuple):
            value = match[0] or match[1]
        else:
            value = match
        if value and value not in out:
            out.append(value)
    return out


def extract_topic_from_rst(rst_path: Path) -> str:
    """Extract the first top-level heading as the exemplar topic."""
    lines = rst_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if index + 1 >= len(lines):
            continue
        underline = lines[index + 1].strip()
        if (
            len(underline) >= 3
            and underline == underline[0] * len(underline)
            and underline[0] in "=-~^"
        ):
            return line.strip()
    return ""


def validate_fls_matching() -> dict[str, Any]:
    """Run FLS matching validation against exemplar ground truth."""
    manifest = json.loads(EXEMPLAR_MANIFEST.read_text(encoding="utf-8"))
    entries = manifest.get("exemplars") if isinstance(manifest, dict) else []
    if not isinstance(entries, list):
        entries = []

    results: list[dict[str, Any]] = []
    matches = 0
    total = 0

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        relative_path = str(entry.get("path", "")).strip()
        if not relative_path:
            continue
        rst_path = GUIDELINES_REPO / relative_path
        if not rst_path.exists():
            continue

        ground_truth_ids = extract_fls_ids_from_rst(rst_path)
        if not ground_truth_ids:
            continue

        topic = extract_topic_from_rst(rst_path)
        if not topic:
            continue

        try:
            predicted = resolve_fls_for_construct(topic.split())
        except RuntimeError:
            predicted = {"paragraph_id": "fls_UNRESOLVED", "chapter": ""}

        predicted_id = str(predicted.get("paragraph_id", "fls_UNRESOLVED"))
        matched = predicted_id in ground_truth_ids
        if matched:
            matches += 1
        total += 1

        results.append(
            {
                "exemplar": relative_path,
                "topic": topic,
                "ground_truth_ids": ground_truth_ids,
                "predicted_id": predicted_id,
                "predicted_chapter": str(predicted.get("chapter", "")),
                "match": matched,
            }
        )

    ratio = (matches / total) if total else 0.0
    return {
        "total_exemplars_with_fls": total,
        "top1_matches": matches,
        "top1_accuracy": matches,
        "accuracy_ratio": ratio,
        "results": results,
    }


def main() -> int:
    report = validate_fls_matching()
    print("FLS Matching Validation")
    print(f"  Exemplars with :fls: directives: {report['total_exemplars_with_fls']}")
    print(f"  Top-1 matches: {report['top1_matches']}/{report['total_exemplars_with_fls']}")
    print(f"  Accuracy: {report['accuracy_ratio']:.1%}")

    out = Path(".cache/sqlite_kb/reports/fls_matching_validation.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
