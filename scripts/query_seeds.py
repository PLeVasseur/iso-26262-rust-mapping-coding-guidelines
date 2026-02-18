#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from _common import (
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    extract_json_blob,
    read_yaml,
    repo_root,
    resolve_extractor_paths,
    run_command,
    utc_now,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic seed queries")
    parser.add_argument("--run-id", required=True, help="Run ID for output folder naming")
    parser.add_argument("--corpus-pack", required=True, help="Corpus pack id from config registry")
    parser.add_argument("--profile", choices=["quick", "full"], default="quick")
    parser.add_argument("--seed-set", default="default")
    parser.add_argument(
        "--semantic-model-id",
        default=os.environ.get("SEMANTIC_MODEL_ID", "miniLM-L6-v2-local-v1"),
        help="Semantic model id for concept/hybrid retrieval",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Override output directory; defaults to .cache/seed_runs/<run_id>",
    )
    return parser.parse_args()


def select_queries(
    queries: list[dict[str, Any]],
    profile: str,
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    if profile == "full":
        return queries

    quick_policy = policy.get("profiles", {}).get("quick", {})
    if quick_policy.get("enforce_full_seed_manifest", False):
        return queries

    exact = [query for query in queries if query.get("query_kind") == "exact"]
    concept = [query for query in queries if query.get("query_kind") == "concept"]
    return exact[:8] + concept[:4]


def build_query_command(
    cache_root: Path,
    query: dict[str, Any],
    semantic_model_id: str,
) -> list[str]:
    query_kind = query["query_kind"]
    retrieval_mode = "lexical" if query_kind == "exact" else "hybrid"

    command = [
        "cargo",
        "run",
        "--quiet",
        "--",
        "query",
        "--cache-root",
        str(cache_root),
        "--query",
        str(query["query_text"]),
        "--part",
        str(query["part"]),
        "--retrieval-mode",
        retrieval_mode,
        "--with-ancestors",
        "--with-descendants",
        "--with-pinpoint",
        "--json",
        "--limit",
        str(query.get("limit", 3)),
    ]

    chunk_type = query.get("chunk_type")
    if chunk_type:
        command.extend(["--type", str(chunk_type)])

    node_type = query.get("node_type")
    if node_type:
        command.extend(["--node-type", str(node_type)])

    if retrieval_mode != "lexical":
        command.extend(["--semantic-model-id", semantic_model_id])

    return command


def load_corpus_pack(root: Path, corpus_pack_id: str) -> dict[str, Any]:
    registry_path = root / "config" / "corpus_registry.yaml"
    registry = read_yaml(registry_path)
    for entry in registry["corpus_packs"]:
        if entry["corpus_pack_id"] == corpus_pack_id:
            if not entry.get("enabled", False):
                raise ValueError(f"corpus pack is disabled: {corpus_pack_id}")
            return entry
    raise ValueError(f"corpus pack not found: {corpus_pack_id}")


def main() -> int:
    args = parse_args()
    root = repo_root()
    paths = resolve_extractor_paths(root)
    output_dir = args.output_dir or (root / ".cache" / "seed_runs" / args.run_id)
    query_output_dir = output_dir / "queries"
    query_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        pack = load_corpus_pack(root, args.corpus_pack)
        manifest_path = root / pack["seed_manifest_path"]
        manifest = read_yaml(manifest_path)
        policy = read_yaml(root / "config" / "change_growth_policy.yaml")
    except Exception as exc:
        print(f"[seed-query][error] failed to load config: {exc}")
        return EXIT_RUNTIME_FAIL

    seed_set = manifest["seed_sets"].get(args.seed_set)
    if seed_set is None:
        print(f"[seed-query][error] seed set not found: {args.seed_set}")
        return EXIT_RUNTIME_FAIL

    selected_queries = select_queries(seed_set["queries"], args.profile, policy)
    entries: list[dict[str, Any]] = []

    for query in selected_queries:
        query_id = query["query_id"]
        command = build_query_command(paths.cache_root, query, args.semantic_model_id)
        result = run_command(command, cwd=paths.repo_root)
        if result.returncode != 0:
            print(f"[seed-query][error] query failed: {query_id}")
            print(result.stderr)
            return EXIT_RUNTIME_FAIL

        try:
            payload = extract_json_blob(result.stdout)
        except Exception as exc:
            print(f"[seed-query][error] invalid JSON for query {query_id}: {exc}")
            return EXIT_RUNTIME_FAIL

        payload_path = query_output_dir / f"{query_id}.json"
        write_json(payload_path, payload)

        entries.append(
            {
                "query_id": query_id,
                "query_text": query["query_text"],
                "query_kind": query["query_kind"],
                "part": query["part"],
                "result_count": payload.get("returned", 0),
                "output_path": str(payload_path.relative_to(root)),
            }
        )

    manifest_payload = {
        "version": 1,
        "generated_at": utc_now(),
        "run_id": args.run_id,
        "corpus_pack": args.corpus_pack,
        "profile": args.profile,
        "seed_set": args.seed_set,
        "query_total": len(entries),
        "entries": entries,
    }
    write_json(output_dir / "seed_query_manifest.json", manifest_payload)
    print(
        f"[seed-query] completed {len(entries)} queries -> "
        f"{(output_dir / 'seed_query_manifest.json').relative_to(root)}"
    )
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
