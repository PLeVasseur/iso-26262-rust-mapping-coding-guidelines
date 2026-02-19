#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from _common import EXIT_RUNTIME_FAIL, EXIT_SUCCESS, read_yaml, repo_root, utc_now, write_yaml
from _fls_proxy import slug_ascii


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build normalized Rust grounding signal pack")
    parser.add_argument(
        "--sources",
        type=Path,
        default=Path("config/rust_signal_sources.yaml"),
    )
    parser.add_argument("--output", type=Path, default=Path("data/rust_signals.yaml"))
    return parser.parse_args()


def dedup_list(values: list[Any]) -> list[str]:
    output: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in output:
            output.append(text)
    return output


def normalize_topic(entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    topic_key = str(entry.get("topic_key") or "").strip()
    topic_id = slug_ascii(topic_key)
    payload = {
        "topic_key": topic_key,
        "match_terms": dedup_list(entry.get("match_terms") or []),
        "std_refs": dedup_list(entry.get("std_refs") or []),
        "concept_terms": dedup_list(entry.get("concept_terms") or []),
        "preferred_lints": dedup_list(entry.get("preferred_lints") or []),
        "verification_terms": dedup_list(entry.get("verification_terms") or []),
    }
    return topic_id, payload


def normalize_fls_override(entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    key = str(entry.get("fls_match") or "").strip().lower()
    payload = {
        "std_refs": dedup_list(entry.get("std_refs") or []),
        "concept_terms": dedup_list(entry.get("concept_terms") or []),
        "preferred_lints": [],
        "verification_terms": [],
    }
    return key, payload


def main() -> int:
    args = parse_args()
    root = repo_root()
    source_path = root / args.sources
    if not source_path.exists():
        print(f"[rust-signals][error] missing sources file: {source_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL

    payload = read_yaml(source_path) or {}
    source_id = str(payload.get("source_id") or "rust-signal-sources").strip()

    topic_signals: dict[str, dict[str, Any]] = {}
    for topic in payload.get("topics", []):
        if not isinstance(topic, dict):
            continue
        topic_id, topic_payload = normalize_topic(topic)
        if not topic_id:
            continue
        topic_signals[topic_id] = topic_payload

    fls_signals: dict[str, dict[str, Any]] = {}
    for override in payload.get("fls_ref_overrides", []):
        if not isinstance(override, dict):
            continue
        key, override_payload = normalize_fls_override(override)
        if not key:
            continue
        fls_signals[key] = override_payload

    obligation_class_signals: dict[str, dict[str, Any]] = {}
    for class_name, class_payload in (payload.get("obligation_class_overrides") or {}).items():
        class_key = str(class_name).strip().lower()
        if not class_key or not isinstance(class_payload, dict):
            continue
        obligation_class_signals[class_key] = {
            "evidence_anchor_classes": dedup_list(
                class_payload.get("evidence_anchor_classes") or []
            ),
            "verification_terms": dedup_list(class_payload.get("verification_terms") or []),
        }

    global_defaults = payload.get("global_defaults") or {}
    output_payload = {
        "version": 1,
        "generated_at": utc_now(),
        "source_id": source_id,
        "topic_signals": dict(sorted(topic_signals.items())),
        "fls_signals": dict(sorted(fls_signals.items())),
        "obligation_class_signals": dict(sorted(obligation_class_signals.items())),
        "global_defaults": {
            "fallback_std_refs": dedup_list(global_defaults.get("fallback_std_refs") or []),
            "fallback_concept_terms": dedup_list(
                global_defaults.get("fallback_concept_terms") or []
            ),
        },
    }

    output_path = root / args.output
    write_yaml(output_path, output_payload)
    print(
        "[rust-signals] wrote "
        f"topics={len(output_payload['topic_signals'])} "
        f"fls_overrides={len(output_payload['fls_signals'])} "
        f"-> {output_path.relative_to(root)}"
    )
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
