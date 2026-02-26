#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from retrieval.guidelines.build_runner import run_guidelines_build

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3

MANAGED_BEGIN = ".. BEGIN MANAGED GUIDELINE SIDECARS"
MANAGED_END = ".. END MANAGED GUIDELINE SIDECARS"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _digest_files(paths: list[Path]) -> str:
    hasher = hashlib.sha256()
    for path in sorted(paths):
        hasher.update(path.as_posix().encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def _normalize_content(value: str) -> str:
    lines = [
        line.rstrip() for line in str(value).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


INLINE_ROLE_RE = re.compile(r":[a-zA-Z0-9_\-]+:`([^`]+)`")


def _sanitize_text(value: str) -> str:
    normalized = _normalize_content(value)
    sanitized = INLINE_ROLE_RE.sub(r"``\1``", normalized)
    return sanitized.replace(".. _", "")


def _render_guideline(
    title: str, blocks: list[tuple[str, int, str]], bibliography: list[tuple[str, str]]
) -> str:
    rendered_title = f"[Generated] {title}"
    payload: list[str] = [rendered_title, "=" * len(rendered_title), ""]
    label_map = {
        "rationale": "Rationale",
        "compliant": "Compliant Example",
        "non_compliant": "Non-Compliant Example",
        "body": "Guideline",
    }
    for block_type, _, content in blocks:
        heading = label_map.get(block_type, block_type.replace("_", " ").title())
        payload.append(f"**{heading}**")
        payload.append("")
        payload.append(".. code-block:: text")
        payload.append("")
        for line in _sanitize_text(content).split("\n"):
            payload.append(f"   {line}")
        payload.append("")

    if bibliography:
        payload.append("**Bibliography**")
        payload.append("")
        for key, content in sorted(bibliography):
            payload.append(f"- {key}: {_sanitize_text(content)}")
        payload.append("")
    return "\n".join(payload).strip() + "\n"


def _managed_block(entries: list[str]) -> list[str]:
    lines = [
        MANAGED_BEGIN,
        ".. toctree::",
        "   :maxdepth: 1",
        "",
    ]
    for entry in entries:
        lines.append(f"   {entry}")
    lines.extend(["", MANAGED_END])
    return lines


def _extract_managed_entries(existing_lines: list[str]) -> tuple[list[str], int, int]:
    try:
        start = existing_lines.index(MANAGED_BEGIN)
        end = existing_lines.index(MANAGED_END)
    except ValueError:
        return [], -1, -1
    entries: list[str] = []
    for line in existing_lines[start + 1 : end]:
        stripped = line.strip()
        if not stripped or stripped.startswith(".. toctree::") or stripped.startswith(":"):
            continue
        if stripped.startswith(".."):
            continue
        entries.append(stripped)
    return entries, start, end


def _sync_chapter_index(index_path: Path, entries: list[str]) -> tuple[list[str], bool]:
    existing_lines = (
        index_path.read_text(encoding="utf-8").splitlines() if index_path.exists() else []
    )
    old_entries, start, end = _extract_managed_entries(existing_lines)
    existing_references: set[str] = set()
    for idx, line in enumerate(existing_lines):
        if start >= 0 and end >= 0 and start <= idx <= end:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("..") or stripped.startswith(":"):
            continue
        existing_references.add(stripped)
    managed_entries = [entry for entry in entries if entry not in existing_references]
    managed_lines = _managed_block(managed_entries)
    if start >= 0 and end >= 0 and start < end:
        new_lines = existing_lines[:start] + managed_lines + existing_lines[end + 1 :]
    else:
        if existing_lines and existing_lines[-1].strip() != "":
            existing_lines.append("")
        new_lines = existing_lines + managed_lines
    index_path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    return old_entries, True


def export_guidelines(*, db_path: Path, output_root: Path) -> dict[str, object]:
    output_root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    written_files: list[Path] = []
    chapter_entries: dict[str, list[str]] = {}
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

            if not path.exists():
                path.write_text(_render_guideline(title, blocks, bibliography), encoding="utf-8")
            written_files.append(path)
            stem = Path(filename).stem
            chapter_entries.setdefault(chapter, []).append(stem)
    finally:
        connection.close()

    for chapter, stems in sorted(chapter_entries.items()):
        chapter_root = output_root / chapter
        index_path = chapter_root / "index.rst"
        _sync_chapter_index(index_path, sorted(set(stems)))
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
