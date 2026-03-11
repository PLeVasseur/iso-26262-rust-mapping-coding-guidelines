#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from retrieval.guidelines.build_runner import run_guidelines_build
from retrieval.operations.chapter_index_policy import ensure_glob_toctree
from retrieval.operations.guideline_template_bridge import (
    build_template_guideline_page,
    parse_bibliography_payload,
)
from retrieval.services.guideline_fls_resolution import get_guideline_fls_resolution_state

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _digest_files(paths: list[Path]) -> str:
    hasher = hashlib.sha256()
    for path in sorted(paths):
        hasher.update(path.as_posix().encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def _block_lookup(blocks: list[tuple[str, int, str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for block_type, _, content in blocks:
        lookup[str(block_type)] = str(content)
    return lookup


def export_guidelines(*, db_path: Path, output_root: Path) -> dict[str, object]:
    output_root.mkdir(parents=True, exist_ok=True)
    guidelines_repo_root = output_root.parents[1]
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    written_files: list[Path] = []
    touched_chapters: set[str] = set()
    try:
        rows = connection.execute(
            """
            SELECT guideline_id, title, export_topic, metadata_json
            FROM guideline_records
            ORDER BY guideline_id ASC
            """
        ).fetchall()
        for row in rows:
            guideline_id = str(row[0])
            title = str(row[1])
            chapter = str(row[2]).strip() or "general"
            metadata_raw = str(row[3] or "{}").strip() or "{}"
            try:
                metadata = json.loads(metadata_raw)
            except json.JSONDecodeError:
                metadata = {}
            filename = str(metadata.get("export_filename", "")).strip() or f"{guideline_id}.rst"
            if not filename.endswith(".rst"):
                filename = f"{filename}.rst"
            chapter_root = output_root / chapter
            chapter_root.mkdir(parents=True, exist_ok=True)
            path = chapter_root / filename

            blocks = connection.execute(
                """
                SELECT block_type, order_index, content
                FROM guideline_blocks
                WHERE guideline_id = ?
                ORDER BY order_index ASC
                """,
                (guideline_id,),
            ).fetchall()
            bibliography = connection.execute(
                """
                SELECT b.bib_key, b.content
                FROM guideline_bib_links AS l
                JOIN guideline_bibliography AS b ON b.bib_key = l.bib_key
                WHERE l.guideline_id = ?
                ORDER BY b.bib_key ASC
                """,
                (guideline_id,),
            ).fetchall()

            block_map = _block_lookup(blocks)
            tags = [
                str(tag).strip() for tag in list(metadata.get("tags") or []) if str(tag).strip()
            ]
            bibliography_entries: list[tuple[str, str, str, str]] = []
            for _, content in bibliography:
                parsed = parse_bibliography_payload(str(content))
                if parsed is not None:
                    bibliography_entries.append(parsed)

            rendered = build_template_guideline_page(
                guidelines_repo_root=guidelines_repo_root,
                guideline_id=guideline_id,
                title=title,
                category=str(metadata.get("category", "advisory") or "advisory"),
                status=str(metadata.get("status", "draft") or "draft"),
                release=str(metadata.get("release", "1.85.1") or "1.85.1"),
                fls_id=str(
                    get_guideline_fls_resolution_state(guideline_id, db_path=db_path).get(
                        "effective_fls_id", ""
                    )
                ),
                decidability=str(metadata.get("decidability", "undecidable") or "undecidable"),
                scope=str(metadata.get("scope", "module") or "module"),
                tags=tags,
                amplification=str(block_map.get("body", "")).strip(),
                rationale=str(block_map.get("rationale", "")).strip(),
                non_compliant_examples=[
                    (
                        str(block_map.get("non_compliant_narrative", "")).strip(),
                        str(block_map.get("non_compliant_code", "")).strip(),
                    )
                ],
                compliant_examples=[
                    (
                        str(block_map.get("compliant_narrative", "")).strip(),
                        str(block_map.get("compliant_code", "")).strip(),
                    )
                ],
                bibliography_entries=bibliography_entries,
                non_compliant_miri_intent=str(metadata.get("non_compliant_miri_intent", "") or ""),
                compliant_miri_intent=str(metadata.get("compliant_miri_intent", "") or ""),
            )

            path.write_text(rendered, encoding="utf-8")
            touched_chapters.add(chapter)
            written_files.append(path)
    finally:
        connection.close()

    chapter_roots = sorted((output_root / chapter) for chapter in touched_chapters)
    for chapter_root in chapter_roots:
        index_path = chapter_root / "index.rst"
        changed = ensure_glob_toctree(index_path)
        if changed or index_path.exists():
            written_files.append(index_path)

    return {
        "file_count": len(written_files),
        "output_digest": _digest_files(written_files) if written_files else "",
        "generated_files": [str(path) for path in sorted(set(written_files))],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export deterministic guidelines RST from SQLite")
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--repo-root", default="")
    parser.add_argument("--no-build-gate", action="store_true")
    parser.add_argument("--ignore-spec-lock-diff", action="store_true")
    parser.add_argument("--ignore-spec-lock-diff-reason", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db_path).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    try:
        export_summary = export_guidelines(db_path=db_path, output_root=output_root)
        build_status = 0
        build_stdout = ""
        build_stderr = ""
        if not bool(args.no_build_gate) and str(args.repo_root).strip():
            repo_root = Path(str(args.repo_root)).expanduser().resolve()
            build_status, build_stdout, build_stderr, _ = run_guidelines_build(
                repo_root=repo_root,
                offline=True,
            )
            if build_status != 0:
                raise RuntimeError(
                    json.dumps(
                        {
                            "failure_code": "POST_EXPORT_BUILD_FAILED",
                            "stdout": build_stdout,
                            "stderr": build_stderr,
                        }
                    )
                )

        summary = {
            "exported_at": _utc_now(),
            "db_path": str(db_path),
            "output_root": str(output_root),
            "build_gate_enabled": not bool(args.no_build_gate),
            "ignore_spec_lock_diff": bool(args.ignore_spec_lock_diff),
            "ignore_spec_lock_diff_reason": str(args.ignore_spec_lock_diff_reason),
            **export_summary,
            "build_status": build_status,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return EXIT_SUCCESS
    except Exception as exc:  # pragma: no cover
        print(f"[export-rst][error] {exc}")
        return EXIT_RUNTIME_FAIL


if __name__ == "__main__":
    sys.exit(main())
