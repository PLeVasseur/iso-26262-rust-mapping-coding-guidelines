#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

from _common import (
    EXIT_POLICY_FAIL,
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    load_guidelines_payload,
    read_yaml,
    repo_root,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check FLS proxy span and chapter coverage")
    parser.add_argument("--todo-guidelines", type=Path, default=Path("data/todo_guidelines.yaml"))
    parser.add_argument("--coverage-matrix", type=Path, default=Path("data/coverage_matrix.csv"))
    parser.add_argument("--fls-inventory", type=Path, default=Path("data/fls_inventory.yaml"))
    parser.add_argument(
        "--fls-target-candidates",
        type=Path,
        default=Path("data/fls_target_candidates.yaml"),
    )
    parser.add_argument("--policy", type=Path, default=Path("config/completeness_policy.yaml"))
    parser.add_argument("--enforce", action="store_true")
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = repo_root()

    required_paths = [
        root / args.todo_guidelines,
        root / args.coverage_matrix,
        root / args.fls_inventory,
        root / args.fls_target_candidates,
        root / args.policy,
    ]
    missing = [path for path in required_paths if not path.exists()]
    if missing:
        for path in missing:
            print(f"[fls-proxy][error] missing required input: {path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL

    guideline_payload = load_guidelines_payload(root / args.todo_guidelines)
    inventory_payload = read_yaml(root / args.fls_inventory) or {}
    candidates_payload = read_yaml(root / args.fls_target_candidates) or {}
    policy = read_yaml(root / args.policy) or {}

    paragraphs = inventory_payload.get("paragraphs") or []
    paragraph_to_chapter = {
        str(item.get("fls_ref")): str(item.get("chapter_id"))
        for item in paragraphs
        if item.get("fls_ref") and item.get("chapter_id")
    }
    valid_refs = set(paragraph_to_chapter.keys())

    guidelines = guideline_payload.get("guidelines") or []
    guideline_ref_map: dict[str, set[str]] = {}
    warnings: list[str] = []
    errors: list[str] = []

    for guideline in guidelines:
        guideline_id = str(guideline.get("id") or "").strip()
        if not guideline_id:
            continue
        refs = {str(ref).strip() for ref in guideline.get("fls_refs", []) if str(ref).strip()}
        guideline_ref_map[guideline_id] = refs
        invalid = sorted(ref for ref in refs if ref not in valid_refs)
        for ref in invalid:
            warnings.append(f"guideline `{guideline_id}` references unknown fls_ref `{ref}`")

    target_refs_from_guidelines: dict[str, set[str]] = defaultdict(set)
    with (root / args.coverage_matrix).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            target_id = str(row.get("target_id") or "").strip()
            guideline_id = str(row.get("guideline_id") or "").strip()
            if not target_id or not guideline_id:
                continue
            target_refs_from_guidelines[target_id].update(
                guideline_ref_map.get(guideline_id, set())
            )

    target_candidates = candidates_payload.get("target_candidates") or []
    fls_span_thresholds = policy.get("fls_span_min_by_target_class") or {}
    chapter_threshold = float(policy.get("fls_chapter_coverage_min") or 0.0)
    hard_fail = (
        args.enforce or str((policy.get("gate_modes") or {}).get("fls_proxy") or "warn") == "error"
    )

    span_results = []
    in_scope_chapters: set[str] = set()

    for entry in target_candidates:
        target_id = str(entry.get("target_id") or "").strip()
        target_class = str(entry.get("target_class") or "clause")
        candidate_refs = {
            str(item.get("fls_ref"))
            for item in entry.get("candidate_fls_refs", [])
            if str(item.get("fls_ref") or "")
        }
        candidate_refs = {ref for ref in candidate_refs if ref in valid_refs}
        if not target_id or not candidate_refs:
            continue

        for ref in candidate_refs:
            chapter_id = paragraph_to_chapter.get(ref)
            if chapter_id:
                in_scope_chapters.add(chapter_id)

        covered_refs = target_refs_from_guidelines.get(target_id, set()) & candidate_refs
        ratio = len(covered_refs) / len(candidate_refs)
        threshold = float(fls_span_thresholds.get(target_class, 0.0))
        ok = ratio >= threshold

        span_results.append(
            {
                "target_id": target_id,
                "target_class": target_class,
                "candidate_count": len(candidate_refs),
                "covered_count": len(covered_refs),
                "ratio": round(ratio, 4),
                "threshold": threshold,
                "ok": ok,
            }
        )

        if not ok:
            message = (
                f"target `{target_id}` fls span {ratio:.3f} < {threshold:.3f} "
                f"({len(covered_refs)}/{len(candidate_refs)})"
            )
            if hard_fail:
                errors.append(message)
            else:
                warnings.append(message)

    covered_chapters = {
        paragraph_to_chapter[ref]
        for refs in guideline_ref_map.values()
        for ref in refs
        if ref in paragraph_to_chapter and paragraph_to_chapter[ref] in in_scope_chapters
    }

    chapter_ratio = 1.0
    if in_scope_chapters:
        chapter_ratio = len(covered_chapters) / len(in_scope_chapters)
    chapter_ok = chapter_ratio >= chapter_threshold
    if not chapter_ok:
        message = (
            f"chapter coverage {chapter_ratio:.3f} < {chapter_threshold:.3f} "
            f"({len(covered_chapters)}/{len(in_scope_chapters)})"
        )
        if hard_fail:
            errors.append(message)
        else:
            warnings.append(message)

    report = {
        "target_count": len(span_results),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "gate_mode": "error" if hard_fail else "warn",
        "chapter_coverage": {
            "in_scope": len(in_scope_chapters),
            "covered": len(covered_chapters),
            "ratio": round(chapter_ratio, 4),
            "threshold": chapter_threshold,
            "ok": chapter_ok,
        },
        "targets": sorted(span_results, key=lambda item: item["target_id"]),
        "errors": errors,
        "warnings": warnings,
        "ok": len(errors) == 0,
    }

    if args.json_output:
        write_json(args.json_output, report)

    if errors:
        print(f"[fls-proxy] failed with {len(errors)} error(s)")
        for error in errors:
            print(f"[fls-proxy][error] {error}")
        return EXIT_POLICY_FAIL

    print(
        "[fls-proxy] "
        f"ok targets={report['target_count']} chapter_ratio={report['chapter_coverage']['ratio']} "
        f"warnings={report['warning_count']}"
    )
    for warning in warnings:
        print(f"[fls-proxy][warn] {warning}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
