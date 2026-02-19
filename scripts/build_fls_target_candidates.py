#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from _common import EXIT_RUNTIME_FAIL, EXIT_SUCCESS, read_yaml, repo_root, utc_now, write_yaml
from _fls_proxy import classify_target_class, normalize_obligation_unit, slug_ascii


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Map ISO targets/obligations to candidate FLS refs"
    )
    parser.add_argument("--seed-topics", type=Path, default=Path("data/seed_topics.yaml"))
    parser.add_argument("--fls-inventory", type=Path, default=Path("data/fls_inventory.yaml"))
    parser.add_argument("--output", type=Path, default=Path("data/fls_target_candidates.yaml"))
    parser.add_argument("--max-candidates", type=int, default=3)
    return parser.parse_args()


def tokenize(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if token}


def target_id(seed: dict[str, Any]) -> str:
    anchor = str(seed.get("citation_anchor_id") or "").strip()
    if anchor:
        return anchor
    chunk = str(seed.get("chunk_id") or "").strip()
    return chunk or str(seed.get("seed_id") or "unknown-target")


def chapter_by_slug(chapters: list[dict[str, Any]], slug: str) -> str | None:
    chapter_id = f"fls_ch_{slug_ascii(slug)}"
    for chapter in chapters:
        if chapter.get("chapter_id") == chapter_id:
            return chapter_id
    return None


def fallback_chapter_ids(seed: dict[str, Any], chapters: list[dict[str, Any]]) -> list[str]:
    text = " ".join(
        [
            str(seed.get("topic_phrase") or ""),
            str(seed.get("category_candidate") or ""),
            str(seed.get("context_summary") or ""),
            str(seed.get("reference") or ""),
        ]
    ).lower()

    ordered_slugs: list[str]
    if "concurrency" in text or "atomic" in text or "thread" in text:
        ordered_slugs = ["concurrency", "ownership_and_destruction"]
    elif "error" in text or "defensive" in text or "contract" in text:
        ordered_slugs = ["exceptions_and_errors", "functions"]
    elif "type" in text or "conversion" in text or "cast" in text:
        ordered_slugs = ["types_and_traits", "values"]
    elif "subset" in text or "table" in text or "forbidden" in text:
        ordered_slugs = ["program_structure_and_compilation", "unsafety", "types_and_traits"]
    elif "macro" in text:
        ordered_slugs = ["macros", "program_structure_and_compilation"]
    elif "unsafe" in text or "ub" in text:
        ordered_slugs = ["unsafety", "types_and_traits"]
    else:
        ordered_slugs = ["program_structure_and_compilation", "statements"]

    chapter_ids = []
    for slug in ordered_slugs:
        chapter_id = chapter_by_slug(chapters, slug)
        if chapter_id:
            chapter_ids.append(chapter_id)

    if chapter_ids:
        return chapter_ids

    if chapters:
        return [str(chapters[0].get("chapter_id"))]
    return []


def paragraph_score(seed: dict[str, Any], paragraph: dict[str, Any]) -> float:
    seed_text = " ".join(
        [
            str(seed.get("topic_phrase") or ""),
            str(seed.get("category_candidate") or ""),
            str(seed.get("context_summary") or ""),
            str(seed.get("reference") or ""),
            str(seed.get("iso_ref") or ""),
        ]
    )
    seed_tokens = tokenize(seed_text)
    keyword_tokens = {
        token for item in paragraph.get("keywords", []) for token in tokenize(str(item))
    }
    if not keyword_tokens:
        return 0.0

    overlap = seed_tokens & keyword_tokens
    if not overlap:
        return 0.0
    base = len(overlap) / len(keyword_tokens)

    topic = str(seed.get("category_candidate") or "").lower()
    chapter_id = str(paragraph.get("chapter_id") or "")
    if "language subset" in topic and chapter_id in {
        "fls_ch_program_structure_and_compilation",
        "fls_ch_unsafety",
        "fls_ch_types_and_traits",
    }:
        base += 0.15
    if "defensive" in topic and chapter_id == "fls_ch_exceptions_and_errors":
        base += 0.15
    if "concurrency" in topic and chapter_id == "fls_ch_concurrency":
        base += 0.2

    return min(base, 0.99)


def build_candidate_refs(
    seed: dict[str, Any],
    paragraphs: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    max_candidates: int,
) -> list[dict[str, Any]]:
    scored = []
    for paragraph in paragraphs:
        score = paragraph_score(seed, paragraph)
        if score <= 0:
            continue
        scored.append(
            {
                "fls_ref": str(paragraph.get("fls_ref")),
                "chapter_id": str(paragraph.get("chapter_id")),
                "confidence": round(score, 4),
                "rationale": "keyword-overlap",
            }
        )

    scored.sort(key=lambda item: (-item["confidence"], item["fls_ref"]))
    if scored:
        return scored[:max_candidates]

    chapter_ids = fallback_chapter_ids(seed, chapters)
    fallback = []
    for chapter_id in chapter_ids:
        paragraph = next(
            (
                item
                for item in paragraphs
                if item.get("chapter_id") == chapter_id
                and str(item.get("fls_ref", "")).endswith("_core")
            ),
            None,
        )
        if paragraph is None:
            continue
        fallback.append(
            {
                "fls_ref": str(paragraph.get("fls_ref")),
                "chapter_id": chapter_id,
                "confidence": 0.2,
                "rationale": "category-fallback",
            }
        )
    return fallback[:max_candidates]


def main() -> int:
    args = parse_args()
    root = repo_root()
    seed_topics_path = root / args.seed_topics
    fls_inventory_path = root / args.fls_inventory

    if not seed_topics_path.exists():
        print(f"[fls-candidates][error] missing seed topics: {seed_topics_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL
    if not fls_inventory_path.exists():
        print(
            f"[fls-candidates][error] missing FLS inventory: {fls_inventory_path.relative_to(root)}"
        )
        return EXIT_RUNTIME_FAIL

    seed_payload = read_yaml(seed_topics_path) or {}
    inventory_payload = read_yaml(fls_inventory_path) or {}
    seeds = seed_payload.get("seed_topics") or []
    paragraphs = inventory_payload.get("paragraphs") or []
    chapters = inventory_payload.get("chapters") or []

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for seed in seeds:
        sid = str(seed.get("seed_id") or "").strip()
        if not sid:
            continue

        target = target_id(seed)
        obligation = str(seed.get("obligation_unit_id") or "").strip() or normalize_obligation_unit(
            seed
        )
        target_class = classify_target_class(target)
        refs = build_candidate_refs(seed, paragraphs, chapters, max_candidates=args.max_candidates)
        if not refs:
            continue

        key = (target, obligation)
        entry = grouped.get(key)
        if entry is None:
            entry = {
                "target_id": target,
                "obligation_unit_id": obligation,
                "target_class": target_class,
                "seed_ids": [],
                "candidate_fls_refs": [],
            }
            grouped[key] = entry

        entry["seed_ids"].append(sid)
        existing_ref_map = {
            str(item.get("fls_ref")): item
            for item in entry["candidate_fls_refs"]
            if isinstance(item, dict)
        }
        for candidate in refs:
            ref = candidate["fls_ref"]
            existing = existing_ref_map.get(ref)
            if existing is None or candidate["confidence"] > existing["confidence"]:
                existing_ref_map[ref] = candidate
        merged = sorted(
            existing_ref_map.values(),
            key=lambda item: (-item["confidence"], item["fls_ref"]),
        )
        entry["candidate_fls_refs"] = merged[: args.max_candidates]

    target_candidates = []
    for (_, _), entry in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
        entry["seed_ids"] = sorted(set(entry["seed_ids"]))
        target_candidates.append(entry)

    payload = {
        "version": 1,
        "generated_at": utc_now(),
        "source_run_id": str(seed_payload.get("run_id") or "unknown-run"),
        "target_candidates": target_candidates,
    }

    output_path = root / args.output
    write_yaml(output_path, payload)
    print(
        "[fls-candidates] wrote "
        f"targets={len(target_candidates)} -> {output_path.relative_to(root)}"
    )
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
