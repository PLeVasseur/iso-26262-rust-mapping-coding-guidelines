#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

from _common import EXIT_RUNTIME_FAIL, EXIT_SUCCESS, read_json, repo_root, write_yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize query outputs into seed_topics.yaml")
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--seed-run-dir",
        type=Path,
        help="Override seed run directory; defaults to .cache/seed_runs/<run_id>",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/seed_topics.yaml"),
        help="Output file path relative to repo root",
    )
    return parser.parse_args()


def classify_category(query_text: str) -> str:
    lowered = query_text.lower()
    if "complexity" in lowered:
        return "Complexity & structure"
    if "language subset" in lowered or "table 1" in lowered:
        return "Language subset / forbidden constructs"
    if "type" in lowered or "conversion" in lowered:
        return "Type safety & conversions"
    if "defensive" in lowered or "error" in lowered:
        return "Defensive programming & contracts"
    if "concurrency" in lowered:
        return "Concurrency and shared-state constraints"
    return "Tooling, compliance evidence, and deviations"


def classify_enforcement_hint(category: str) -> str:
    if category in {
        "Style guide & formatting",
        "Language subset / forbidden constructs",
        "Type safety & conversions",
    }:
        return "AUTO"
    if category in {"Concurrency and shared-state constraints"}:
        return "HYBRID"
    return "AUDIT"


def seed_key(result: dict[str, Any]) -> str:
    part = str(result.get("part", ""))
    reference = str(result.get("reference", ""))
    citation_anchor_id = str(result.get("citation_anchor_id", ""))
    row_key = ""
    pinpoint_units = result.get("pinpoint_units") or []
    if pinpoint_units:
        row_key = str(pinpoint_units[0].get("row_key") or "")
    return "|".join([part, reference, citation_anchor_id, row_key])


def stable_seed_id(key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"SEED-{digest.upper()}"


def main() -> int:
    args = parse_args()
    root = repo_root()
    seed_run_dir = args.seed_run_dir or (root / ".cache" / "seed_runs" / args.run_id)
    manifest_path = seed_run_dir / "seed_query_manifest.json"
    if not manifest_path.exists():
        print(f"[seed-build][error] missing seed query manifest: {manifest_path}")
        return EXIT_RUNTIME_FAIL

    manifest = read_json(manifest_path)
    dedup: dict[str, dict[str, Any]] = {}

    for entry in manifest.get("entries", []):
        output_path = root / entry["output_path"]
        payload = read_json(output_path)
        query_text = entry["query_text"]
        query_kind = entry["query_kind"]
        category = classify_category(query_text)
        enforcement_hint = classify_enforcement_hint(category)

        for result in payload.get("results", []):
            if query_kind == "exact":
                reference_norm = str(result.get("reference") or "").lower()
                heading_norm = str(result.get("heading") or "").lower()
                query_norm = query_text.lower()
                if query_norm not in reference_norm and query_norm not in heading_norm:
                    continue

            key = seed_key(result)
            if key in dedup:
                continue

            citation = (
                result.get("citation")
                or f"{result.get('doc_id', '')}, {result.get('reference', '')}"
            )
            reference = str(result.get("reference") or "")
            part = result.get("part")
            anchor = str(result.get("citation_anchor_id") or "")
            row_key = ""
            pinpoint_units = result.get("pinpoint_units") or []
            if pinpoint_units:
                row_key = str(pinpoint_units[0].get("row_key") or "")

            dedup[key] = {
                "seed_id": stable_seed_id(key),
                "iso_ref": f"Part {part} {reference}".strip(),
                "chunk_id": str(result.get("chunk_id") or "unknown"),
                "citation": citation,
                "topic_phrase": query_text,
                "context_summary": (
                    f"Seed derived from query '{query_text}' and reference '{reference}'."
                ),
                "category_candidate": category,
                "enforceability_hint": enforcement_hint,
                "part": part,
                "reference": reference,
                "citation_anchor_id": anchor,
                "row_key": row_key,
            }

    payload = {
        "version": 1,
        "run_id": args.run_id,
        "seed_topics": sorted(dedup.values(), key=lambda value: value["seed_id"]),
    }
    output_path = root / args.output
    write_yaml(output_path, payload)
    print(f"[seed-build] wrote {len(payload['seed_topics'])} topics")
    print(f"[seed-build] output -> {output_path.relative_to(root)}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
