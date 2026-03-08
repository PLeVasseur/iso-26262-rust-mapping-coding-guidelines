from __future__ import annotations

import json
import re
import shutil
from argparse import Namespace
from pathlib import Path
from typing import Any

import yaml

from retrieval.operations.export_rst import export_guidelines
from retrieval.writer_host.conformance import run_conformance
from retrieval.writer_host.publish_git import (
    create_worktree,
    finalize_commit,
    push_branch,
    remove_worktree,
    status_porcelain,
)
from retrieval.writer_host.publish_ingest import ingest_records
from retrieval.writer_host.publish_loader import load_publish_payload
from retrieval.writer_host.publish_mapping import map_publish_record

_BIBENTRY_LINE_RE = re.compile(r"^(\s*\* - :bibentry:`)(gui_[^:]+:)([^`]+)(`)\s*$")
_BIBDESC_LINE_RE = re.compile(r"^(\s*-\s+)(.*?)(https?://\S+)\s*$")


def _load_guidelines_repo_root(root: Path) -> Path:
    cfg_path = root / "config" / "corpora" / "guidelines_repo.yaml"
    payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    sources = payload.get("sources") if isinstance(payload, dict) else {}
    repo_raw = str((sources or {}).get("guidelines_repo_root", "")).strip()
    if not repo_raw:
        raise RuntimeError("sources.guidelines_repo_root is required")
    return (root / repo_raw).resolve()


def _build_record(row: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    amplification = row["amplification"]
    rationale = row["rationale"]
    examples = row["examples"]
    metadata = row["metadata"]
    draft = dict(row.get("draft") or {})
    return {
        "target_id": mapping["target_id"],
        "atom_id": mapping.get("atom_id", ""),
        "draft_id": mapping.get("draft_id", ""),
        "guideline_id": mapping["guideline_id"],
        "filename": mapping["filename"],
        "chapter": mapping["chapter"],
        "title": mapping["title"],
        "category": mapping["category"],
        "status": mapping["status"],
        "release": mapping["release"],
        "fls_id": mapping["fls_id"],
        "fls_resolution": dict(mapping.get("fls_resolution") or {}),
        "fls_resolution_report": str(mapping.get("fls_resolution_report") or ""),
        "publishability": dict(mapping.get("publishability") or {}),
        "decidability": mapping["decidability"],
        "scope": mapping["scope"],
        "tags": list(mapping["tags"]),
        "source_plan_id": str(draft.get("source_plan_id", "")).strip(),
        "review_question": str(draft.get("review_question", "")).strip(),
        "curation_disposition": str(draft.get("curation_disposition", "")).strip(),
        "curation_reason": str(draft.get("curation_reason", "")).strip(),
        "non_compliant_miri_intent": str(examples.get("non_compliant_miri_intent", "")).strip(),
        "compliant_miri_intent": str(examples.get("compliant_miri_intent", "")).strip(),
        "non_compliant_miri_skip_justification": str(
            examples.get("non_compliant_miri_skip_justification", "")
        ).strip(),
        "compliant_miri_skip_justification": str(
            examples.get("compliant_miri_skip_justification", "")
        ).strip(),
        "blocks": [
            {
                "block_type": "body",
                "order_index": 1,
                "content": str(amplification.get("guideline_amplification_text", "")).strip(),
            },
            {
                "block_type": "rationale",
                "order_index": 2,
                "content": str(rationale.get("rationale_text", "")).strip(),
            },
            {
                "block_type": "non_compliant_narrative",
                "order_index": 3,
                "content": str(examples.get("non_compliant_narrative", "")).strip(),
            },
            {
                "block_type": "non_compliant_code",
                "order_index": 4,
                "content": str(examples.get("non_compliant_code", "")).strip(),
            },
            {
                "block_type": "compliant_narrative",
                "order_index": 5,
                "content": str(examples.get("compliant_narrative", "")).strip(),
            },
            {
                "block_type": "compliant_code",
                "order_index": 6,
                "content": str(examples.get("compliant_code", "")).strip(),
            },
        ],
        "bibliography_rows": list(metadata.get("bibliography_rows") or []),
    }


def publish_root_for_run(*, root: Path, run_dir: Path) -> Path:
    return root / ".cache" / "sqlite_kb" / "reports" / "writer_publish" / run_dir.name


def default_publish_report_path(*, root: Path, run_dir: Path) -> Path:
    return publish_root_for_run(root=root, run_dir=run_dir) / "writer_publish_report.json"


def _copy_export_snapshot(*, source_root: Path, snapshot_root: Path) -> dict[str, Any]:
    if snapshot_root.exists():
        shutil.rmtree(snapshot_root)
    shutil.copytree(source_root, snapshot_root)
    files = sorted(path for path in snapshot_root.rglob("*") if path.is_file())
    return {
        "path": str(snapshot_root),
        "file_count": len(files),
        "files": [str(path) for path in files],
    }


def _relative_export_paths(*, generated_files: list[str], source_root: Path) -> list[str]:
    out: list[str] = []
    for raw in generated_files:
        candidate = Path(str(raw)).resolve()
        try:
            rel = candidate.relative_to(source_root)
        except ValueError:
            continue
        out.append(rel.as_posix())
    return sorted(dict.fromkeys(out))


def _classify_export_delta(
    *, worktree_root: Path, source_root: Path, generated_files: list[str]
) -> dict[str, Any]:
    relative_generated = _relative_export_paths(
        generated_files=generated_files, source_root=source_root
    )
    if not relative_generated:
        return {
            "snapshot_root": "",
            "source_worktree": str(worktree_root),
            "generated_files": [],
            "created_files": [],
            "modified_files": [],
            "deleted_files": [],
            "unchanged_generated_files": [],
            "counts": {
                "generated": 0,
                "created": 0,
                "modified": 0,
                "deleted": 0,
                "unchanged_generated": 0,
            },
        }

    rows = status_porcelain(
        worktree_root=worktree_root,
        pathspecs=[f"src/coding-guidelines/{path}" for path in relative_generated],
    )
    status_by_path = {
        Path(str(row.get("path", ""))).relative_to("src/coding-guidelines").as_posix(): row
        for row in rows
        if str(row.get("path", "")).startswith("src/coding-guidelines/")
    }

    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    unchanged: list[str] = []
    for path in relative_generated:
        row = status_by_path.get(path)
        if row is None:
            unchanged.append(path)
            continue
        code = str(row.get("code", "  "))
        if "?" in code or "A" in code:
            created.append(path)
        elif "D" in code:
            deleted.append(path)
        elif "M" in code or "R" in code or "C" in code:
            modified.append(path)
        else:
            unchanged.append(path)

    return {
        "snapshot_root": "",
        "source_worktree": str(worktree_root),
        "generated_files": relative_generated,
        "created_files": created,
        "modified_files": modified,
        "deleted_files": deleted,
        "unchanged_generated_files": unchanged,
        "counts": {
            "generated": len(relative_generated),
            "created": len(created),
            "modified": len(modified),
            "deleted": len(deleted),
            "unchanged_generated": len(unchanged),
        },
    }


def _write_export_delta_manifest(*, publish_root: Path, payload: dict[str, Any]) -> Path:
    path = publish_root / "exported_guidelines_changes.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


def _render_export_delta_note(*, payload: dict[str, Any], manifest_path: Path) -> str:
    counts = dict(payload.get("counts") or {})
    created = list(payload.get("created_files") or [])
    modified = list(payload.get("modified_files") or [])
    deleted = list(payload.get("deleted_files") or [])
    unchanged = list(payload.get("unchanged_generated_files") or [])

    def _section(title: str, paths: list[str]) -> list[str]:
        lines = [f"## {title}", ""]
        if not paths:
            lines.append("- none")
        else:
            lines.extend(f"- `{path}`" for path in paths)
        lines.append("")
        return lines

    lines = [
        "# This Run Changes",
        "",
        "This snapshot contains the full exported `src/coding-guidelines` tree for durability.",
        "The lists below identify the files changed by this publish run.",
        "",
        f"- Manifest: `{manifest_path.name}`",
        f"- Generated files: {int(counts.get('generated', 0))}",
        f"- Created files: {int(counts.get('created', 0))}",
        f"- Modified files: {int(counts.get('modified', 0))}",
        f"- Deleted files: {int(counts.get('deleted', 0))}",
        f"- Unchanged generated files: {int(counts.get('unchanged_generated', 0))}",
        "",
    ]
    lines.extend(_section("Created Files", created))
    lines.extend(_section("Modified Files", modified))
    if deleted:
        lines.extend(_section("Deleted Files", deleted))
    else:
        lines.extend(["## Deleted Files", "", "- none", ""])
    lines.extend(["## Unchanged Generated Files", ""])
    if unchanged:
        lines.append(f"- count: {len(unchanged)}")
    else:
        lines.append("- count: 0")
    lines.append("")
    return "\n".join(lines)


def _write_export_delta_note(
    *, snapshot_root: Path, payload: dict[str, Any], manifest_path: Path
) -> Path:
    note_path = snapshot_root / "THIS_RUN_CHANGES.md"
    note_path.write_text(
        _render_export_delta_note(payload=payload, manifest_path=manifest_path),
        encoding="utf-8",
    )
    return note_path


def _write_run_scoped_report(*, run_dir: Path, name: str, payload: dict[str, Any]) -> Path:
    path = run_dir / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


def _write_internal_rendered_candidate_manifest(
    *,
    publish_root: Path,
    source_root: Path,
    mapped_records: list[dict[str, Any]],
    delta_payload: dict[str, Any],
) -> Path:
    generated = set(str(value) for value in list(delta_payload.get("generated_files") or []))
    rendered_candidates: list[dict[str, Any]] = []
    for record in mapped_records:
        chapter = str(record.get("chapter", "")).strip()
        filename = str(record.get("filename", "")).strip()
        relative = f"{chapter}/{filename}" if chapter and filename else ""
        rendered_path = relative if relative and relative in generated else ""
        rendered_candidates.append(
            {
                "draft_id": str(record.get("draft_id", "")).strip(),
                "atom_id": str(record.get("atom_id", "")).strip(),
                "target_id": str(record.get("target_id", "")).strip(),
                "guideline_id": str(record.get("guideline_id", "")).strip(),
                "rendered_path": rendered_path,
                "chapter": chapter,
                "title": str(record.get("title", "")).strip(),
                "admissibility_status": "",
            }
        )
    payload = {
        "run_id": publish_root.name,
        "internal_render_root": str(source_root),
        "rendered_candidates": rendered_candidates,
        "unrendered_candidates": [
            row["draft_id"]
            for row in rendered_candidates
            if not str(row.get("rendered_path", "")).strip()
        ],
        "notes": "Rendered candidates remain visible internally even when later excluded from external reviewer packets.",
    }
    path = publish_root / "internal_rendered_candidate_manifest.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


def _cleanup_report(*, requested: bool, performed: bool, reason: str) -> dict[str, Any]:
    return {
        "requested": requested,
        "performed": performed,
        "reason": reason,
    }


def _canonicalize_exported_bibliography(
    *, source_root: Path, generated_files: list[str]
) -> dict[str, Any]:
    generated_paths = {
        Path(path).resolve() for path in generated_files if Path(path).suffix == ".rst"
    }
    all_rst_paths = sorted(path for path in source_root.rglob("*.rst") if path.is_file())
    if not all_rst_paths:
        return {"status": "skipped", "updated_files": [], "updated_entry_count": 0}

    def _iter_entries(path: Path) -> list[dict[str, Any]]:
        lines = path.read_text(encoding="utf-8").splitlines()
        out: list[dict[str, Any]] = []
        for index, line in enumerate(lines[:-1]):
            key_match = _BIBENTRY_LINE_RE.match(line)
            desc_match = _BIBDESC_LINE_RE.match(lines[index + 1])
            if key_match is None or desc_match is None:
                continue
            out.append(
                {
                    "guideline_prefix": key_match.group(2),
                    "suffix": key_match.group(3),
                    "url": desc_match.group(3),
                    "description": desc_match.group(2).rstrip(),
                    "key_line_index": index,
                    "desc_line_index": index + 1,
                }
            )
        return out

    canonical_by_url: dict[str, tuple[str, str]] = {}
    ordered_paths = [path for path in all_rst_paths if path not in generated_paths] + [
        path for path in all_rst_paths if path in generated_paths
    ]
    for path in ordered_paths:
        for entry in _iter_entries(path):
            url = str(entry["url"])
            canonical_by_url.setdefault(url, (str(entry["suffix"]), str(entry["description"])))

    updated_files: list[str] = []
    updated_entries = 0
    for path in sorted(generated_paths):
        lines = path.read_text(encoding="utf-8").splitlines()
        entries = _iter_entries(path)
        replacements: dict[str, str] = {}
        changed = False
        for entry in entries:
            url = str(entry["url"])
            canonical = canonical_by_url.get(url)
            if canonical is None:
                continue
            canonical_suffix, canonical_description = canonical
            current_suffix = str(entry["suffix"])
            current_description = str(entry["description"])
            if current_suffix == canonical_suffix and current_description == canonical_description:
                continue
            key_match = _BIBENTRY_LINE_RE.match(lines[int(entry["key_line_index"])] or "")
            desc_match = _BIBDESC_LINE_RE.match(lines[int(entry["desc_line_index"])] or "")
            if key_match is None or desc_match is None:
                continue
            guideline_prefix = str(entry["guideline_prefix"])
            lines[int(entry["key_line_index"])] = (
                f"{key_match.group(1)}{guideline_prefix}{canonical_suffix}{key_match.group(4)}"
            )
            lines[int(entry["desc_line_index"])] = (
                f"{desc_match.group(1)}{canonical_description} {url}"
            )
            replacements[current_suffix] = canonical_suffix
            updated_entries += 1
            changed = True
        if changed:
            text = "\n".join(lines) + "\n"
            guideline_id = path.stem
            for old_suffix, new_suffix in replacements.items():
                text = text.replace(
                    f":cite:`{guideline_id}:{old_suffix}`",
                    f":cite:`{guideline_id}:{new_suffix}`",
                )
                text = text.replace(
                    f":bibentry:`{guideline_id}:{old_suffix}`",
                    f":bibentry:`{guideline_id}:{new_suffix}`",
                )
            path.write_text(text, encoding="utf-8")
            updated_files.append(str(path))

    return {
        "status": "patched" if updated_files else "unchanged",
        "updated_files": updated_files,
        "updated_entry_count": updated_entries,
    }


def _prepare_review_mode_worktree(worktree_root: Path) -> dict[str, Any]:
    fls_checks_path = worktree_root / "exts" / "coding_guidelines" / "fls_checks.py"
    if not fls_checks_path.exists():
        return {"status": "skipped", "reason": "fls_checks_missing", "path": str(fls_checks_path)}

    original = fls_checks_path.read_text(encoding="utf-8")
    if "OPENCODE_ALLOW_REVIEW_UNRESOLVED_FLS" in original:
        return {"status": "already_patched", "path": str(fls_checks_path)}

    import_anchor = "import json\nimport re\n"
    helper_anchor = "\n\nclass FLSValidationError(SphinxError):\n"
    condition_anchor = "            # Check if the FLS ID exists in the gathered IDs\n"
    if (
        import_anchor not in original
        or helper_anchor not in original
        or condition_anchor not in original
    ):
        raise RuntimeError(f"unable to patch review-mode FLS compatibility in {fls_checks_path}")

    patched = original.replace(import_anchor, "import json\nimport os\nimport re\n", 1)
    patched = patched.replace(
        helper_anchor,
        "\n\ndef _allow_review_unresolved_fls() -> bool:\n"
        '    return os.environ.get("OPENCODE_ALLOW_REVIEW_UNRESOLVED_FLS", "").strip() == "1"\n'
        "\n"
        "\nclass FLSValidationError(SphinxError):\n",
        1,
    )
    patched = patched.replace(
        condition_anchor,
        '            if fls_value == "fls_UNRESOLVED" and _allow_review_unresolved_fls():\n'
        "                logger.info(\n"
        '                    f"Need {need_id} retains fls_UNRESOLVED placeholder in review mode"\n'
        "                )\n"
        "                continue\n"
        "\n" + condition_anchor,
        1,
    )
    fls_checks_path.write_text(patched, encoding="utf-8")
    return {"status": "patched", "path": str(fls_checks_path)}


def _base_publish_report(
    *,
    root: Path,
    run_dir: Path,
    mode: str,
    dry_run: bool,
    keep_worktree: bool,
) -> dict[str, Any]:
    publish_root = publish_root_for_run(root=root, run_dir=run_dir)
    return {
        "status": "fail",
        "mode": mode,
        "run_dir": str(run_dir),
        "repo_root": "",
        "publish_root": str(publish_root),
        "db_path": str(publish_root / "writer_publish.sqlite"),
        "dry_run": dry_run,
        "keep_worktree": keep_worktree,
        "worktree": "",
        "branch": "",
        "failure_code": "",
        "failure_message": "",
        "ingest": {},
        "export": {},
        "export_snapshot": {},
        "export_delta": {},
        "review_mode_worktree": {},
        "bibliography_canonicalization": {},
        "publishability_audit": {},
        "conformance": {},
        "commit": {"committed": False},
        "push": {"pushed": False},
        "cleanup": _cleanup_report(
            requested=not keep_worktree, performed=False, reason="not_started"
        ),
    }


def _ingest_mapped_rows(
    *,
    run_dir: Path,
    mode: str,
    output_db: Path,
    mapped_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = ingest_records(
        db_path=output_db,
        records=mapped_rows,
        source_run_id=run_dir.name,
    )
    metrics = {
        "unsafe_examples_total": 0,
        "miri_check_count": 0,
        "miri_expect_ub_count": 0,
        "miri_skip_count": 0,
        "miri_skip_without_justification_count": 0,
    }
    for row in mapped_rows:
        for side in ("non_compliant", "compliant"):
            code = str(
                next(
                    (
                        block.get("content", "")
                        for block in list(row.get("blocks") or [])
                        if str(block.get("block_type", "")) == f"{side}_code"
                    ),
                    "",
                )
            )
            intent = str(row.get(f"{side}_miri_intent", "")).strip().lower()
            justification = str(row.get(f"{side}_miri_skip_justification", "")).strip()
            if "unsafe" in code:
                metrics["unsafe_examples_total"] += 1
            if intent == "check":
                metrics["miri_check_count"] += 1
            elif intent == "expect_ub":
                metrics["miri_expect_ub_count"] += 1
            elif intent == "skip":
                metrics["miri_skip_count"] += 1
                if not justification:
                    metrics["miri_skip_without_justification_count"] += 1

    return {
        "status": "pass",
        "run_dir": str(run_dir),
        "mode": mode,
        "db": summary,
        "record_count": len(mapped_rows),
        "annotation_policy_metrics": metrics,
    }


def run_ingest_from_run(
    *,
    root: Path,
    run_dir: Path,
    mode: str,
    output_db: Path,
    resolution_report_root: Path | None = None,
    allow_unresolved: bool = False,
) -> dict[str, Any]:
    payload = load_publish_payload(run_dir=run_dir, publishable=(mode == "publishable"))
    mapped_rows: list[dict[str, Any]] = []
    for row in payload["draft_rows"]:
        mapping = map_publish_record(
            row,
            resolution_report_root=resolution_report_root,
            allow_unresolved=allow_unresolved,
        )
        mapped_rows.append(_build_record(row, mapping))
    _ = root
    return _ingest_mapped_rows(
        run_dir=run_dir, mode=mode, output_db=output_db, mapped_rows=mapped_rows
    )


def _build_publishability_audit(
    *,
    run_dir: Path,
    mode: str,
    publish_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = load_publish_payload(run_dir=run_dir, publishable=(mode == "publishable"))
    resolution_root = publish_root / "fls_resolution"
    rows: list[dict[str, Any]] = []
    for draft_row in payload["draft_rows"]:
        mapping = map_publish_record(
            draft_row,
            resolution_report_root=resolution_root,
            allow_unresolved=True,
        )
        publishability = dict(mapping.get("publishability") or {})
        rows.append(
            {
                "target_id": str(mapping.get("target_id", "")),
                "guideline_id": str(mapping.get("guideline_id", "")),
                "filename": str(mapping.get("filename", "")),
                "chapter": str(mapping.get("chapter", "")),
                "title": str(mapping.get("title", "")),
                "publishable": bool(publishability.get("publishable", False)),
                "reason_code": str(publishability.get("reason_code", "")),
                "reason": str(publishability.get("reason", "")),
                "resolved_paragraph_id": str(publishability.get("resolved_paragraph_id", "")),
                "report_path": str(publishability.get("report_path", "")),
                "mapping": mapping,
                "row": draft_row,
            }
        )
    blocked = [row for row in rows if not bool(row.get("publishable", False))]
    reason_counts: dict[str, int] = {}
    for row in blocked:
        code = str(row.get("reason_code", "UNRESOLVED")) or "UNRESOLVED"
        reason_counts[code] = reason_counts.get(code, 0) + 1
    audit = {
        "run_dir": str(run_dir),
        "mode": mode,
        "draft_count": len(rows),
        "publishable_count": len(rows) - len(blocked),
        "blocked_count": len(blocked),
        "status": "pass" if not blocked else "blocked",
        "reason_counts": reason_counts,
        "rows": [
            {key: value for key, value in row.items() if key not in {"mapping", "row"}}
            for row in rows
        ],
    }
    path = publish_root / "publishability_audit.json"
    path.write_text(json.dumps(audit, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    audit["path"] = str(path)
    return audit, rows


def run_export_rst(*, root: Path, db_path: Path, guidelines_repo_root: Path) -> dict[str, Any]:
    output_root = guidelines_repo_root / "src" / "coding-guidelines"
    summary = export_guidelines(db_path=db_path, output_root=output_root)
    return {
        "status": "pass",
        "db_path": str(db_path),
        "output_root": str(output_root),
        "export": summary,
    }


def run_publish_from_run(
    *,
    root: Path,
    run_dir: Path,
    mode: str,
    dry_run: bool,
    keep_worktree: bool = False,
    audit_only: bool = False,
) -> dict[str, Any]:
    repo_root = _load_guidelines_repo_root(root)
    publish_root = publish_root_for_run(root=root, run_dir=run_dir)
    publish_root.mkdir(parents=True, exist_ok=True)
    db_path = publish_root / "writer_publish.sqlite"
    report = _base_publish_report(
        root=root,
        run_dir=run_dir,
        mode=mode,
        dry_run=dry_run,
        keep_worktree=keep_worktree,
    )
    report["repo_root"] = str(repo_root)

    try:
        audit, audited_rows = _build_publishability_audit(
            run_dir=run_dir,
            mode=mode,
            publish_root=publish_root,
        )
        report["publishability_audit"] = audit
    except Exception as exc:
        report["failure_code"] = "PUBLISHABILITY_AUDIT_FAILED"
        report["failure_message"] = str(exc)
        return report

    if audit_only:
        blocked = int(audit.get("blocked_count", 0))
        report["status"] = "publishability_pass" if blocked == 0 else "publishability_blocked"
        report["failure_code"] = "" if blocked == 0 else "PUBLISHABILITY_BLOCKED"
        report["failure_message"] = (
            "" if blocked == 0 else f"{blocked} targets blocked publishable export"
        )
        report["cleanup"] = _cleanup_report(
            requested=not keep_worktree,
            performed=False,
            reason="audit_only_no_worktree",
        )
        return report

    if mode == "publishable" and int(audit.get("blocked_count", 0)) > 0:
        blocked = int(audit.get("blocked_count", 0))
        report["status"] = "publishability_blocked"
        report["failure_code"] = "PUBLISHABILITY_BLOCKED"
        report["failure_message"] = f"{blocked} targets blocked publishable export"
        report["cleanup"] = _cleanup_report(
            requested=not keep_worktree,
            performed=False,
            reason="publishability_blocked_before_worktree",
        )
        return report

    if dry_run:
        report["status"] = "dry_run"
        report["cleanup"] = _cleanup_report(
            requested=not keep_worktree,
            performed=False,
            reason="dry_run_no_worktree",
        )
        return report

    worktree_info = create_worktree(repo_root=repo_root, cache_root=publish_root)
    worktree_root = Path(str(worktree_info["worktree"])).resolve()
    branch = str(worktree_info["branch"])
    report["worktree"] = str(worktree_root)
    report["branch"] = branch
    cleanup_performed = False
    cleanup_reason = "preserved_for_review"
    try:
        if mode in {"review", "review-internal", "review-external"}:
            try:
                report["review_mode_worktree"] = _prepare_review_mode_worktree(worktree_root)
            except Exception as exc:
                report["failure_code"] = "REVIEW_MODE_WORKTREE_PREP_FAILED"
                report["failure_message"] = str(exc)
                return report

        try:
            mapped_records = [_build_record(row["row"], row["mapping"]) for row in audited_rows]
            ingest = _ingest_mapped_rows(
                run_dir=run_dir,
                mode=mode,
                output_db=db_path,
                mapped_rows=mapped_records,
            )
            report["ingest"] = ingest
            (publish_root / "annotation_policy_metrics.json").write_text(
                json.dumps(ingest.get("annotation_policy_metrics", {}), indent=2, sort_keys=False)
                + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            report["failure_code"] = "INGEST_FAILED"
            report["failure_message"] = str(exc)
            return report

        try:
            export = run_export_rst(root=root, db_path=db_path, guidelines_repo_root=worktree_root)
            report["export"] = export
            source_root = worktree_root / "src" / "coding-guidelines"
            report["bibliography_canonicalization"] = _canonicalize_exported_bibliography(
                source_root=source_root,
                generated_files=list(((export.get("export") or {}).get("generated_files") or [])),
            )
            delta_payload = _classify_export_delta(
                worktree_root=worktree_root,
                source_root=source_root,
                generated_files=list(((export.get("export") or {}).get("generated_files") or [])),
            )
            snapshot = _copy_export_snapshot(
                source_root=source_root,
                snapshot_root=publish_root / "exported_guidelines",
            )
            delta_payload["snapshot_root"] = str(snapshot["path"])
            manifest_path = _write_export_delta_manifest(
                publish_root=publish_root, payload=delta_payload
            )
            note_path = _write_export_delta_note(
                snapshot_root=Path(str(snapshot["path"])),
                payload=delta_payload,
                manifest_path=manifest_path,
            )
            snapshot_files = list(snapshot.get("files") or [])
            snapshot_files.append(str(note_path))
            snapshot["files"] = sorted(dict.fromkeys(snapshot_files))
            snapshot["file_count"] = len(snapshot["files"])
            internal_manifest_path = _write_internal_rendered_candidate_manifest(
                publish_root=publish_root,
                source_root=source_root,
                mapped_records=mapped_records,
                delta_payload=delta_payload,
            )
            report["export_snapshot"] = snapshot
            report["export_delta"] = {
                **delta_payload,
                "manifest_path": str(manifest_path),
                "note_path": str(note_path),
                "internal_rendered_candidate_manifest_path": str(internal_manifest_path),
            }
        except Exception as exc:
            report["failure_code"] = "EXPORT_FAILED"
            report["failure_message"] = str(exc)
            return report

        conformance = run_conformance(
            repo_root=worktree_root,
            report_dir=publish_root,
            mode=mode,
        )
        report["conformance"] = conformance
        _write_run_scoped_report(
            run_dir=run_dir,
            name="writer_conformance_report.json",
            payload=conformance,
        )
        if str(conformance.get("status", "")) != "pass":
            report["failure_code"] = "CONFORMANCE_FAILED"
            report["failure_message"] = f"{mode} mode requires passing conformance"
            return report

        if mode != "publishable":
            report["status"] = "review_export_pass"
            report["failure_code"] = ""
            report["failure_message"] = ""
            if not keep_worktree:
                remove_worktree(repo_root=repo_root, worktree_root=worktree_root)
                cleanup_performed = True
                cleanup_reason = "review_export_cleanup"
            else:
                cleanup_reason = "kept_by_request"
            return report

        commit_message = f"feat(guidelines): publish writer run {run_dir.name}"
        try:
            commit = finalize_commit(worktree_root=worktree_root, message=commit_message)
        except Exception as exc:
            report["failure_code"] = "COMMIT_FAILED"
            report["failure_message"] = str(exc)
            return report
        report["commit"] = commit
        if not bool(commit.get("committed", False)):
            report["status"] = "no_changes"
            report["failure_code"] = "NO_CHANGES"
            report["failure_message"] = "export completed but produced no git diff"
            return report

        try:
            push = push_branch(worktree_root=worktree_root, branch=branch)
        except Exception as exc:
            report["failure_code"] = "PUSH_FAILED"
            report["failure_message"] = str(exc)
            return report
        report["push"] = push
        report["status"] = "pass"
        report["failure_code"] = ""
        report["failure_message"] = ""
        if not keep_worktree:
            remove_worktree(repo_root=repo_root, worktree_root=worktree_root)
            cleanup_performed = True
            cleanup_reason = "success_cleanup"
        else:
            cleanup_reason = "kept_by_request"
        return report
    finally:
        if not cleanup_performed:
            if keep_worktree:
                cleanup_reason = "kept_by_request"
            elif report.get("status") != "pass":
                cleanup_reason = "preserved_after_non_pass"
            else:
                cleanup_reason = "already_removed"
        report["cleanup"] = _cleanup_report(
            requested=not keep_worktree,
            performed=cleanup_performed,
            reason=cleanup_reason,
        )


def write_publish_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def run_conformance_command(*, root: Path, run_dir: Path, mode: str) -> dict[str, Any]:
    repo_root = _load_guidelines_repo_root(root)
    report_dir = run_dir
    report = run_conformance(repo_root=repo_root, report_dir=report_dir, mode=mode)
    return {
        "status": report.get("status", "fail"),
        "mode": mode,
        "run_dir": str(run_dir),
        "repo_root": str(repo_root),
        "report_path": str(report_dir / "writer_conformance_report.json"),
        "report": report,
    }


def namespace_from_args(args: Namespace, *, root: Path) -> tuple[Path, str, bool, bool, bool]:
    run_dir_raw = str(getattr(args, "run_dir", "") or "").strip()
    if not run_dir_raw:
        raise RuntimeError("--run-dir is required")
    run_dir = Path(run_dir_raw).resolve()
    if not run_dir.exists():
        raise RuntimeError(f"run_dir does not exist: {run_dir}")
    mode = str(getattr(args, "mode", "publishable") or "publishable")
    dry_run = bool(getattr(args, "dry_run", False))
    keep_worktree = bool(getattr(args, "keep_worktree", False))
    audit_only = bool(getattr(args, "audit_only", False))
    _ = root
    if mode == "exploratory":
        mode = "review-internal"
    if mode == "review":
        mode = "review-internal"
    return run_dir, mode, dry_run, keep_worktree, audit_only
