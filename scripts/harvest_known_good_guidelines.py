#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from _common import EXIT_RUNTIME_FAIL, EXIT_SUCCESS, read_yaml, repo_root
from known_good_lib import (
    compute_signals,
    extract_title,
    fetch_main_sha,
    fetch_text,
    guideline_id_from_path,
    list_guideline_paths,
    save_manifest,
    save_report,
    signals_match_rule,
    utc_now,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Harvest known-good upstream guidelines")
    parser.add_argument("--policy", type=Path, default=Path("config/known_good_policy.yaml"))
    parser.add_argument("--source-sha")
    parser.add_argument("--tier", choices=["strict", "extended", "all"])
    parser.add_argument("--max-guidelines", type=int)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def load_policy(root: Path, path: Path) -> dict[str, Any]:
    absolute = root / path
    if not absolute.exists():
        raise FileNotFoundError(f"policy file missing: {absolute}")
    payload = read_yaml(absolute) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"invalid policy payload: {absolute}")
    return payload


def tier_for_signals(signals: dict[str, bool], tier_rules: dict[str, Any]) -> str | None:
    strict_rule = tier_rules.get("strict") or {}
    extended_rule = tier_rules.get("extended") or {}
    if signals_match_rule(signals, strict_rule):
        return "strict"
    if signals_match_rule(signals, extended_rule):
        return "extended"
    return None


def should_include(selected_tier: str, tier_filter: str) -> bool:
    if tier_filter == "all":
        return selected_tier in {"strict", "extended"}
    if tier_filter == "strict":
        return selected_tier == "strict"
    if tier_filter == "extended":
        return selected_tier in {"strict", "extended"}
    return False


def main() -> int:
    args = parse_args()
    root = repo_root()

    try:
        policy = load_policy(root, args.policy)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[known-good-harvest][error] {exc}")
        return EXIT_RUNTIME_FAIL

    source_repo = str(policy.get("source_repo") or "").strip()
    source_sha = str(args.source_sha or policy.get("source_sha") or "").strip()
    if not source_sha:
        source_sha = fetch_main_sha(source_repo)
    if not source_repo or not source_sha:
        print("[known-good-harvest][error] source_repo/source_sha unresolved")
        return EXIT_RUNTIME_FAIL

    tier_filter = str(args.tier or policy.get("default_tier") or "all")
    output_root_rel = str(args.output_root or policy.get("output_root") or "benchmarks/known-good")
    manifest_path = root / (args.manifest or Path(output_root_rel) / "manifest.yaml")
    report_path = root / (
        args.report or Path(output_root_rel) / "reports" / "harvest_report.json"
    )

    tier_rules = policy.get("tier_rules") or {}
    max_guidelines = args.max_guidelines or int(policy.get("max_guidelines") or 0)

    try:
        all_paths = list_guideline_paths(source_repo, source_sha)
    except Exception as exc:  # noqa: BLE001
        print(f"[known-good-harvest][error] failed to list guideline paths: {exc}")
        return EXIT_RUNTIME_FAIL

    entries: list[dict[str, Any]] = []
    excluded = 0

    for source_path in all_paths:
        raw_url = (
            "https://raw.githubusercontent.com/"
            f"{source_repo}/{source_sha}/{source_path}"
        )
        try:
            text = fetch_text(raw_url)
        except Exception as exc:  # noqa: BLE001
            print(f"[known-good-harvest][warn] failed to fetch {source_path}: {exc}")
            excluded += 1
            continue

        signals = compute_signals(text)
        selected_tier = tier_for_signals(signals, tier_rules)
        if selected_tier is None or not should_include(selected_tier, tier_filter):
            excluded += 1
            continue

        chapter = source_path.split("/")[2]
        filename = Path(source_path).name
        guideline_id = guideline_id_from_path(source_path)
        local_rst_rel = Path(output_root_rel) / "upstream-rst" / chapter / filename
        local_rst_path = root / local_rst_rel
        local_rst_path.parent.mkdir(parents=True, exist_ok=True)
        local_rst_path.write_text(text, encoding="utf-8")

        title = extract_title(text.splitlines())
        if not title:
            title = guideline_id

        entries.append(
            {
                "guideline_id": guideline_id,
                "chapter": chapter,
                "source_path": source_path,
                "local_rst_path": str(local_rst_rel),
                "tier": selected_tier,
                "title": title,
                "signals": signals,
            }
        )

        if max_guidelines > 0 and len(entries) >= max_guidelines:
            break

    entries.sort(key=lambda item: (item["tier"], item["guideline_id"]))

    pack_prefix = str(policy.get("pack_id_prefix") or "known-good")
    pack_id = f"{pack_prefix}-{source_sha[:12]}"

    manifest = {
        "version": 1,
        "pack_id": pack_id,
        "source_repo": source_repo,
        "source_sha": source_sha,
        "generated_at": utc_now(),
        "selection_policy": {
            "tier_filter": tier_filter,
            "max_guidelines": max_guidelines,
        },
        "guideline_count": len(entries),
        "guidelines": entries,
    }
    save_manifest(manifest_path, manifest)

    report = {
        "version": 1,
        "generated_at": utc_now(),
        "source_repo": source_repo,
        "source_sha": source_sha,
        "pack_id": pack_id,
        "tier_filter": tier_filter,
        "included_count": len(entries),
        "excluded_count": excluded,
        "included_by_tier": {
            "strict": sum(1 for entry in entries if entry["tier"] == "strict"),
            "extended": sum(1 for entry in entries if entry["tier"] == "extended"),
        },
    }
    save_report(report_path, report)

    print(
        "[known-good-harvest] "
        f"included={len(entries)} excluded={excluded} "
        f"pack_id={pack_id}"
    )
    print(f"[known-good-harvest] manifest -> {manifest_path.relative_to(root)}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
