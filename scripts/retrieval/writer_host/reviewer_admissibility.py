from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from retrieval.writer_host.editorial_decomposition import assess_decomposition
from retrieval.writer_host.evidence_precision import evaluate_evidence_precision
from retrieval.writer_host.publish import publish_root_for_run
from retrieval.writer_host.publish_loader import load_publish_payload
from retrieval.writer_host.publish_mapping import map_publish_record
from retrieval.writer_host.reviewer_taxonomy import (
    canonical_reviewer_chapters,
    classify_reviewer_family,
    expected_reviewer_chapter,
    is_canonical_reviewer_chapter,
    normalize_reviewer_chapter,
    root_index_contains_chapter,
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _load_render_manifest(publish_root: Path) -> dict[str, Any]:
    path = publish_root / "internal_rendered_candidate_manifest.json"
    if not path.exists():
        return {"path": str(path), "rendered_candidates": []}
    payload = _read_json(path)
    payload["path"] = str(path)
    return payload


def _rendered_by_draft(render_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in list(render_manifest.get("rendered_candidates") or []):
        if not isinstance(row, dict):
            continue
        draft_id = _clean(row.get("draft_id"))
        if draft_id:
            out[draft_id] = row
    return out


def _infer_metadata_status(
    mapping: dict[str, Any], family_key: str
) -> tuple[str, list[str], dict[str, Any]]:
    category = _clean(mapping.get("category")).lower() or "advisory"
    scope = _clean(mapping.get("scope")).lower() or "module"
    decidability = _clean(mapping.get("decidability")).lower() or "undecidable"
    release = _clean(mapping.get("release")) or "1.85.1"
    issues: list[str] = []
    if family_key == "diagnostics_policy" and scope == "module":
        issues.append("scope_too_narrow_for_policy_rule")
    if family_key == "architecture_types" and decidability == "undecidable":
        issues.append("decidability_unreviewed_default")
    if family_key == "strict_provenance" and category == "required":
        issues.append("category_too_strong_for_preference_rule")
    if release == "1.85.1":
        issues.append("release_defaulted")
    status = "pass" if not issues else "review"
    if len(issues) >= 2:
        status = "block"
    return (
        status,
        issues,
        {
            "category": category,
            "scope": scope,
            "decidability": decidability,
            "release": release,
            "defaulted_fields": [
                name
                for name, value in {
                    "release": release == "1.85.1",
                    "scope": scope == "module",
                    "decidability": decidability == "undecidable",
                }.items()
                if value
            ],
        },
    )


def _taxonomy_status(
    *,
    mapping: dict[str, Any],
    row: dict[str, Any],
    rendered: dict[str, Any],
    publish_root: Path,
) -> tuple[str, list[str], dict[str, Any]]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    draft = row.get("draft") if isinstance(row.get("draft"), dict) else {}
    editorial = metadata.get("editorial_metadata") if isinstance(metadata, dict) else {}
    title = _clean(mapping.get("title"))
    tags = [
        str(value).strip()
        for value in list((metadata or {}).get("tags") or [])
        if str(value).strip()
    ]
    constructs = [
        str(value).strip()
        for value in list((draft or {}).get("construct_terms") or [])
        if str(value).strip()
    ]
    chapter_hint = (
        _clean(mapping.get("chapter"))
        or _clean((editorial or {}).get("candidate_chapter"))
        or _clean(draft.get("chapter"))
    )
    expected = expected_reviewer_chapter(
        title=title,
        tags=tags,
        constructs=constructs,
        primary_family=_clean((editorial or {}).get("primary_construct_family")),
        chapter_hint=chapter_hint,
    )
    chapter = normalize_reviewer_chapter(mapping.get("chapter"))
    issues: list[str] = []
    if not is_canonical_reviewer_chapter(chapter):
        issues.append("non_canonical_chapter")
    expected_chapter = _clean(expected.get("chapter"))
    if expected_chapter and expected_chapter != chapter:
        issues.append(f"chapter_mismatch:{expected_chapter}")
    rendered_path = _clean(rendered.get("rendered_path"))
    root_index = publish_root / "exported_guidelines" / "index.rst"
    if root_index.exists() and chapter in canonical_reviewer_chapters():
        if not root_index_contains_chapter(
            index_text=root_index.read_text(encoding="utf-8"), chapter=chapter
        ):
            issues.append("root_index_missing_chapter")
    status = "pass" if not issues else "review"
    if "non_canonical_chapter" in issues or "root_index_missing_chapter" in issues:
        status = "block"
    return (
        status,
        issues,
        {
            "candidate_chapter": chapter,
            "expected_chapter": expected_chapter,
            "rendered_path": rendered_path,
            "reason": _clean(expected.get("reason")),
        },
    )


def _fls_status(mapping: dict[str, Any]) -> tuple[str, list[str]]:
    fls_id = _clean(mapping.get("fls_id"))
    if fls_id and fls_id != "fls_UNRESOLVED":
        return "pass", []
    publishability = dict(mapping.get("publishability") or {})
    reason = _clean(publishability.get("reason_code")) or "UNRESOLVED"
    return "review", [f"fls_unresolved:{reason}"]


def _decomposition_status(row: dict[str, Any], family_key: str) -> tuple[str, list[str]]:
    draft_raw = row.get("draft")
    draft: dict[str, Any]
    if isinstance(draft_raw, dict):
        draft = dict(draft_raw)
    else:
        draft = {}
    metadata_raw = row.get("metadata")
    metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
    editorial = (
        dict(metadata.get("editorial_metadata") or {})
        if isinstance(metadata.get("editorial_metadata"), dict)
        else {}
    )
    narrowed_constructs: list[str] = []
    primary_family = _clean(editorial.get("primary_construct_family"))
    if primary_family:
        narrowed_constructs.append(primary_family)
    narrowed_constructs.extend(
        str(value).strip()
        for value in list(metadata.get("tags") or [])
        if str(value).strip()
        and str(value).strip().lower()
        not in {
            "analyzability",
            "attributes",
            "consistency",
            "diagnostics",
            "expressions",
            "functions",
            "ownership-and-destruction",
            "patterns",
            "style-consistency",
            "types-and-traits",
            "unsafety",
            "verification",
        }
    )
    if not narrowed_constructs:
        narrowed_constructs = [
            str(value).strip() for value in list(draft.get("construct_terms") or [])
        ]
    report = assess_decomposition(
        target_id=_clean(draft.get("target_id")),
        synth={"construct_scope": narrowed_constructs},
        amplification=dict(row.get("amplification") or {}),
        metadata=metadata,
    )
    status = _clean(report.get("status")) or "pass"
    issues = [str(value) for value in list(report.get("issues") or []) if str(value).strip()]
    if status == "split_candidate":
        soft_split_issues = {
            "broad_metadata_surface",
            "multiple_construct_families",
            "multiple_normative_clauses",
        }
        if set(issues).issubset(soft_split_issues):
            return "review", issues
        if (
            family_key in {"diagnostics_policy", "exceptions_errors"}
            and "composite_rule_connectors" not in issues
        ):
            return "review", issues
        if family_key == "architecture_types" and "composite_rule_connectors" not in issues:
            return "review", issues
        return "split_required", issues or ["composite_rule_split_candidate"]
    if status == "review":
        return "review", issues
    return "pass", issues


def _build_duplicate_clusters(entries: list[dict[str, Any]]) -> dict[str, list[int]]:
    clusters: dict[str, list[int]] = {}
    for index, entry in enumerate(entries):
        family_key = _clean(entry.get("guideline_family_key"))
        if not family_key:
            continue
        clusters.setdefault(family_key, []).append(index)
    return {key: value for key, value in clusters.items() if len(value) > 1}


def evaluate_review_admissibility(*, root: Path, run_dir: Path) -> dict[str, Any]:
    publish_root = publish_root_for_run(root=root, run_dir=run_dir)
    payload = load_publish_payload(run_dir=run_dir, publishable=False)
    gate_path = run_dir / "writer_quality_gate_report.json"
    conformance_path = run_dir / "writer_conformance_report.json"
    quality_gate = _read_json(gate_path) if gate_path.exists() else {}
    conformance = _read_json(conformance_path) if conformance_path.exists() else {}
    render_manifest = _load_render_manifest(publish_root)
    rendered_by_draft = _rendered_by_draft(render_manifest)

    entries: list[dict[str, Any]] = []
    metadata_rows: list[dict[str, Any]] = []
    for row in payload["draft_rows"]:
        mapping = map_publish_record(row, allow_unresolved=True)
        draft = dict(row.get("draft") or {})
        metadata = dict(row.get("metadata") or {})
        rendered = rendered_by_draft.get(_clean(draft.get("draft_id")), {})
        title = _clean(mapping.get("title"))
        tags = [
            str(value).strip() for value in list(metadata.get("tags") or []) if str(value).strip()
        ]
        constructs = [
            str(value).strip()
            for value in list(draft.get("construct_terms") or [])
            if str(value).strip()
        ]
        family_key = classify_reviewer_family(title=title, tags=tags, constructs=constructs)

        metadata_status, metadata_issues, metadata_summary = _infer_metadata_status(
            mapping, family_key
        )
        taxonomy_status, taxonomy_issues, taxonomy_summary = _taxonomy_status(
            mapping=mapping,
            row=row,
            rendered=rendered,
            publish_root=publish_root,
        )
        evidence = evaluate_evidence_precision(list(metadata.get("bibliography_rows") or []))
        fls_status, fls_issues = _fls_status(mapping)
        decomposition_status, decomposition_issues = _decomposition_status(row, family_key)

        blocking_reasons: list[str] = []
        warning_reasons: list[str] = []
        if metadata_status == "block":
            blocking_reasons.extend(metadata_issues)
        else:
            warning_reasons.extend(metadata_issues)
        if taxonomy_status == "block":
            blocking_reasons.extend(taxonomy_issues)
        else:
            warning_reasons.extend(taxonomy_issues)
        if evidence.get("blocked"):
            blocking_reasons.extend(list(evidence.get("issues") or []))
        else:
            warning_reasons.extend(list(evidence.get("issues") or []))
        if fls_status != "pass":
            warning_reasons.extend(fls_issues)
        if decomposition_status == "split_required":
            blocking_reasons.extend(decomposition_issues or ["split_required"])
        elif decomposition_issues:
            warning_reasons.extend(decomposition_issues)

        status = "admit"
        if blocking_reasons:
            if decomposition_status == "split_required":
                status = "split_required"
            elif any(reason.startswith("chapter_mismatch:") for reason in blocking_reasons):
                status = "move_required"
            else:
                status = "block"
        elif warning_reasons and taxonomy_status == "review":
            status = "move_required"

        entry = {
            "draft_id": _clean(draft.get("draft_id")),
            "atom_id": _clean(draft.get("atom_id")),
            "target_id": _clean(draft.get("target_id")),
            "guideline_id": _clean(mapping.get("guideline_id")),
            "guideline_family_key": family_key,
            "candidate_title": title,
            "candidate_chapter": _clean(mapping.get("chapter")),
            "normalized_taxonomy_chapter": taxonomy_summary.get("expected_chapter")
            or taxonomy_summary.get("candidate_chapter"),
            "rendered_candidate_path": _clean(rendered.get("rendered_path")),
            "admissibility_status": status,
            "blocking_reasons": sorted(dict.fromkeys(blocking_reasons)),
            "warning_reasons": sorted(dict.fromkeys(warning_reasons)),
            "duplicate_cluster_id": "",
            "composite_cluster_id": family_key if decomposition_status == "split_required" else "",
            "metadata_status": metadata_status,
            "taxonomy_status": taxonomy_status,
            "evidence_precision_status": str(evidence.get("status", "pass")),
            "fls_readiness_status": fls_status,
            "recommended_external_export": "yes" if status == "admit" else "no",
        }
        entries.append(entry)
        metadata_rows.append(
            {
                "draft_id": entry["draft_id"],
                "atom_id": entry["atom_id"],
                "target_id": entry["target_id"],
                "chapter_coherence": taxonomy_summary,
                "status": metadata_status,
                **metadata_summary,
                "field_rationales": {
                    "family_key": family_key,
                    "issues": metadata_issues,
                },
            }
        )

    duplicate_clusters = _build_duplicate_clusters(entries)
    family_resolution_rows: list[dict[str, Any]] = []
    for cluster_id, indices in sorted(duplicate_clusters.items()):
        survivors = sorted(
            indices, key=lambda idx: len(str(entries[idx].get("candidate_title", "")))
        )
        survivor = survivors[0]
        chosen_ids = [entries[survivor]["draft_id"]]
        dropped: list[str] = []
        merge_required: list[str] = []
        for idx in indices:
            entries[idx]["duplicate_cluster_id"] = cluster_id
            if idx == survivor:
                continue
            if cluster_id == "strict_provenance":
                entries[idx]["admissibility_status"] = "merge_required"
                entries[idx]["recommended_external_export"] = "no"
                entries[idx]["blocking_reasons"] = sorted(
                    dict.fromkeys(
                        list(entries[idx]["blocking_reasons"])
                        + ["duplicate_family_survivor_required"]
                    )
                )
                merge_required.append(entries[idx]["draft_id"])
            else:
                entries[idx]["warning_reasons"] = sorted(
                    dict.fromkeys(
                        list(entries[idx]["warning_reasons"]) + ["duplicate_family_review"]
                    )
                )
        family_resolution_rows.append(
            {
                "cluster_id": cluster_id,
                "cluster_kind": "family_duplicate",
                "member_draft_ids": [entries[idx]["draft_id"] for idx in indices],
                "chosen_survivors": chosen_ids,
                "dropped_members": dropped,
                "merge_required_members": merge_required,
                "split_required_members": [
                    entries[idx]["draft_id"]
                    for idx in indices
                    if entries[idx]["admissibility_status"] == "split_required"
                ],
                "rationale": "family clustering applied reviewer-hand-off survivor policy",
            }
        )

    counts_by_status: dict[str, int] = {}
    blocking_reason_counts: dict[str, int] = {}
    for entry in entries:
        status = str(entry.get("admissibility_status", "block"))
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        for reason in list(entry.get("blocking_reasons") or []):
            blocking_reason_counts[str(reason)] = blocking_reason_counts.get(str(reason), 0) + 1

    report_status = "pass" if counts_by_status.get("admit", 0) > 0 else "fail"
    taxonomy_report = {
        "status": "pass",
        "allowed_chapter_roots": list(canonical_reviewer_chapters()),
        "chapter_roots_created_or_modified": sorted(
            {
                str(Path(_clean(entry.get("rendered_candidate_path"))).parts[0])
                for entry in entries
                if _clean(entry.get("rendered_candidate_path"))
            }
        ),
        "orphaned_chapter_roots": sorted(
            {
                _clean(entry.get("candidate_chapter"))
                for entry in entries
                if "non_canonical_chapter" in list(entry.get("blocking_reasons") or [])
            }
        ),
        "non_canonical_roots": sorted(
            {
                _clean(entry.get("candidate_chapter"))
                for entry in entries
                if not is_canonical_reviewer_chapter(entry.get("candidate_chapter"))
            }
        ),
        "admitted_files_by_chapter": {
            chapter: sorted(
                _clean(entry.get("rendered_candidate_path"))
                for entry in entries
                if _clean(entry.get("candidate_chapter")) == chapter
                and _clean(entry.get("recommended_external_export")) == "yes"
            )
            for chapter in canonical_reviewer_chapters()
        },
    }

    return {
        "run_dir": str(run_dir),
        "publish_root": str(publish_root),
        "status": report_status,
        "quality_gate_status": _clean(quality_gate.get("status")) or "not_evaluated",
        "conformance_status": _clean(conformance.get("status")) or "not_evaluated",
        "counts_by_admissibility_status": counts_by_status,
        "counts_by_blocking_reason": blocking_reason_counts,
        "admitted_draft_ids": [
            entry["draft_id"] for entry in entries if entry["recommended_external_export"] == "yes"
        ],
        "blocked_draft_ids": [
            entry["draft_id"] for entry in entries if entry["admissibility_status"] == "block"
        ],
        "merge_required_draft_ids": [
            entry["draft_id"]
            for entry in entries
            if entry["admissibility_status"] == "merge_required"
        ],
        "split_required_draft_ids": [
            entry["draft_id"]
            for entry in entries
            if entry["admissibility_status"] == "split_required"
        ],
        "move_required_draft_ids": [
            entry["draft_id"]
            for entry in entries
            if entry["admissibility_status"] == "move_required"
        ],
        "entries": entries,
        "metadata_adjudication_report": {
            "run_dir": str(run_dir),
            "status": "pass",
            "entries": metadata_rows,
        },
        "taxonomy_integration_report": taxonomy_report,
        "family_resolution_report": {
            "run_dir": str(run_dir),
            "status": "pass",
            "clusters": family_resolution_rows,
        },
        "internal_rendered_candidate_manifest_path": _clean(render_manifest.get("path")),
    }


def write_review_admissibility_artifacts(
    *, run_dir: Path, report: dict[str, Any]
) -> dict[str, str]:
    admissibility_path = run_dir / "writer_review_admissibility_report.json"
    metadata_path = run_dir / "metadata_adjudication_report.json"
    taxonomy_path = run_dir / "taxonomy_integration_report.json"
    family_path = run_dir / "family_resolution_report.json"
    _write_json(admissibility_path, report)
    _write_json(metadata_path, dict(report.get("metadata_adjudication_report") or {}))
    _write_json(taxonomy_path, dict(report.get("taxonomy_integration_report") or {}))
    _write_json(family_path, dict(report.get("family_resolution_report") or {}))
    return {
        "admissibility": str(admissibility_path),
        "metadata": str(metadata_path),
        "taxonomy": str(taxonomy_path),
        "family": str(family_path),
    }
