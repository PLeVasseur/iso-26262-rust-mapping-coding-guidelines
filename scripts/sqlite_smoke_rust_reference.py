#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlite_build_rust_reference import (
    DEFAULT_EXTRACTOR_DB,
    DEFAULT_REFERENCE_CACHE_DIR,
    DEFAULT_REFERENCE_REPO_URL,
    DEFAULT_TABLE_NODE_ID,
    build_rust_reference_db,
)
from sqlite_query_guardrails import GuardrailError, execute_contract_query

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3
EXPECTED_MARKERS = {f"1{chr(ord('a') + idx)}" for idx in range(9)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke checks for rust_reference.sqlite")
    parser.add_argument(
        "--db-path",
        default=".cache/sqlite_kb/current/rust_reference.sqlite",
        help="Path to rust_reference.sqlite",
    )
    parser.add_argument(
        "--contract-path",
        default="config/sqlite_query_contracts/rust_reference.yaml",
        help="Path to rust_reference query contract",
    )
    parser.add_argument(
        "--query-log-root",
        default=".cache/sqlite_kb/query_logs/rust_reference",
        help="Directory for query audit logs",
    )
    parser.add_argument(
        "--snapshot-root",
        default=".cache/sqlite_kb/snapshots/rust_reference",
        help="Snapshot root used when auto-building",
    )
    parser.add_argument(
        "--manifest-path",
        default="data/sqlite_kb_manifest.yaml",
        help="Manifest path used when auto-building",
    )
    parser.add_argument(
        "--extractor-db",
        default=str(DEFAULT_EXTRACTOR_DB),
        help="Extractor sqlite used when auto-building",
    )
    parser.add_argument(
        "--reference-source-dir",
        default=None,
        help="Optional local rust reference source directory",
    )
    parser.add_argument(
        "--reference-cache-dir",
        default=DEFAULT_REFERENCE_CACHE_DIR,
        help="Cache path for rust-lang/reference clone",
    )
    parser.add_argument(
        "--reference-repo-url",
        default=DEFAULT_REFERENCE_REPO_URL,
        help="Git URL for rust reference source",
    )
    parser.add_argument(
        "--reference-revision",
        default=None,
        help="Pinned reference revision (optional)",
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip git fetch before build",
    )
    parser.add_argument(
        "--min-sections",
        type=int,
        default=20,
        help="Minimum sections required during build validation",
    )
    parser.add_argument(
        "--min-statements",
        type=int,
        default=50,
        help="Minimum statements required during build validation",
    )
    parser.add_argument(
        "--min-mechanisms",
        type=int,
        default=6,
        help="Minimum mechanisms required during build validation",
    )
    parser.add_argument(
        "--no-build-if-missing",
        action="store_true",
        help="Fail instead of auto-building when db is absent",
    )
    return parser.parse_args()


def run_smoke(
    db_path: Path,
    contract_path: Path,
    query_log_root: Path,
    snapshot_root: Path,
    manifest_path: Path,
    extractor_db: Path,
    build_if_missing: bool,
    reference_source_dir: Path | None,
    reference_cache_dir: Path,
    reference_repo_url: str,
    reference_revision: str | None,
    skip_fetch: bool,
    min_sections: int,
    min_statements: int,
    min_mechanisms: int,
) -> tuple[bool, str]:
    if not db_path.exists():
        if not build_if_missing:
            return False, f"Database missing: {db_path}"
        build_rust_reference_db(
            db_path=db_path,
            snapshot_root=snapshot_root,
            manifest_path=manifest_path,
            extractor_db=extractor_db,
            table_node_id=DEFAULT_TABLE_NODE_ID,
            reference_source_dir=reference_source_dir,
            reference_cache_dir=reference_cache_dir,
            reference_repo_url=reference_repo_url,
            reference_revision=reference_revision,
            skip_fetch=skip_fetch,
            min_sections=min_sections,
            min_statements=min_statements,
            min_mechanisms=min_mechanisms,
        )

    snapshot_query = execute_contract_query(
        db_path=db_path,
        contract_path=contract_path,
        query_id="snapshot_metadata",
        params={},
        query_log_root=query_log_root,
    )
    if snapshot_query["row_count"] != 1:
        return False, "Expected one snapshot_metadata row"
    snapshot_row = snapshot_query["rows"][0]
    if not str(snapshot_row.get("commit_sha", "")).strip():
        return False, "snapshot_metadata missing commit_sha"
    if not str(snapshot_row.get("fetched_at", "")).strip():
        return False, "snapshot_metadata missing fetched_at"

    chapter_query = execute_contract_query(
        db_path=db_path,
        contract_path=contract_path,
        query_id="chapter_overview",
        params={},
        query_log_root=query_log_root,
    )
    if chapter_query["row_count"] < 4:
        return False, "Too few chapters detected in chapter_overview"

    doc_timestamp_query = execute_contract_query(
        db_path=db_path,
        contract_path=contract_path,
        query_id="document_timestamp_coverage",
        params={},
        query_log_root=query_log_root,
    )
    if doc_timestamp_query["row_count"] != 1:
        return False, "Expected one document_timestamp_coverage row"
    doc_timestamp_row = doc_timestamp_query["rows"][0]
    if int(doc_timestamp_row.get("document_count", 0)) < 1:
        return False, "No documents present in source_documents"
    if int(doc_timestamp_row.get("missing_fetched_at", 0)) != 0:
        return False, "source_documents has missing source_fetched_at values"
    if int(doc_timestamp_row.get("missing_commit_sha", 0)) != 0:
        return False, "source_documents has missing source_commit_sha values"

    verdicts = execute_contract_query(
        db_path=db_path,
        contract_path=contract_path,
        query_id="row_verdicts_for_table1",
        params={},
        query_log_root=query_log_root,
    )
    rows = verdicts["rows"]
    markers = {row["row_marker"] for row in rows}

    if markers != EXPECTED_MARKERS:
        return False, f"Row marker set mismatch: {sorted(markers)}"

    allowed_verdicts = {"applicable", "not_applicable"}
    for row in rows:
        verdict = row["verdict"]
        if verdict not in allowed_verdicts:
            return False, f"Unexpected verdict for {row['row_node_id']}: {verdict}"

        if verdict == "not_applicable":
            if not str(row.get("rationale", "")).strip():
                return False, f"Missing not_applicable rationale for {row['row_node_id']}"
            if not str(row.get("source_anchor", "")).strip():
                return False, f"Missing not_applicable source anchor for {row['row_node_id']}"
            if not str(row.get("rationale_timestamp", "")).strip():
                return False, f"Missing not_applicable rationale timestamp for {row['row_node_id']}"
            continue

        if not str(row.get("rationale_timestamp", "")).strip():
            return False, f"Missing rationale timestamp for {row['row_node_id']}"

        mechanisms = execute_contract_query(
            db_path=db_path,
            contract_path=contract_path,
            query_id="mechanisms_for_row",
            params={"row_node_id": row["row_node_id"]},
            query_log_root=query_log_root,
        )
        if mechanisms["row_count"] < 1:
            return False, f"No mechanisms returned for applicable row {row['row_node_id']}"

        for mechanism_row in mechanisms["rows"]:
            if not str(mechanism_row.get("source_fetched_at", "")).strip():
                return False, f"Missing mechanism source_fetched_at for {row['row_node_id']}"

    return True, "rank-1 rust_reference smoke checks passed"


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]

    db_path = (root / args.db_path).resolve()
    contract_path = (root / args.contract_path).resolve()
    query_log_root = (root / args.query_log_root).resolve()
    snapshot_root = (root / args.snapshot_root).resolve()
    manifest_path = (root / args.manifest_path).resolve()
    extractor_db = Path(args.extractor_db).expanduser().resolve()
    reference_source_dir = (
        Path(args.reference_source_dir).expanduser().resolve()
        if args.reference_source_dir
        else None
    )
    reference_cache_dir = (root / args.reference_cache_dir).resolve()

    try:
        ok, message = run_smoke(
            db_path=db_path,
            contract_path=contract_path,
            query_log_root=query_log_root,
            snapshot_root=snapshot_root,
            manifest_path=manifest_path,
            extractor_db=extractor_db,
            build_if_missing=not args.no_build_if_missing,
            reference_source_dir=reference_source_dir,
            reference_cache_dir=reference_cache_dir,
            reference_repo_url=args.reference_repo_url,
            reference_revision=args.reference_revision,
            skip_fetch=args.skip_fetch,
            min_sections=args.min_sections,
            min_statements=args.min_statements,
            min_mechanisms=args.min_mechanisms,
        )
    except (GuardrailError, OSError, RuntimeError) as exc:
        print(f"[smoke-rust-reference][error] {exc}")
        return EXIT_RUNTIME_FAIL

    if not ok:
        print(f"[smoke-rust-reference][error] {message}")
        return EXIT_RUNTIME_FAIL

    print(f"[smoke-rust-reference][ok] {message}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
