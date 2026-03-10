#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from retrieval.writer_host.fls_calibration import (  # noqa: E402
    extract_fls_ids_from_rst,
    extract_topic_from_rst,
)

DEFAULT_HELDOUT_MANIFEST = ROOT / "data" / "fls_ws7_heldout_manifest.json"
DEFAULT_PUBLISHABILITY_AUDIT = (
    ROOT
    / ".cache"
    / "sqlite_kb"
    / "reports"
    / "writer_publish"
    / "v17_2_closure_23_reviewer_hardened_ws7"
    / "publishability_audit.json"
)
DEFAULT_OUTPUT_PATH = (
    ROOT / ".cache" / "sqlite_kb" / "reports" / "fls_spec" / "ws7_mapping_audit.json"
)
DEFAULT_FLS_DB = ROOT / ".cache" / "sqlite_kb" / "current" / "fls_spec.db"
DEFAULT_GUIDELINES_ROOT = (ROOT / ".." / "safety-critical-rust-coding-guidelines").resolve()

STOPWORDS = {
    "with",
    "from",
    "into",
    "when",
    "must",
    "should",
    "over",
    "their",
    "they",
    "this",
    "that",
    "allow",
    "prefer",
    "explicitly",
    "function",
    "functions",
    "values",
    "rust",
    "language",
    "reference",
    "systems",
    "safety",
}

GENERIC_DOCUMENTS = {"background.html", "general.html", "licenses.html", "glossary.html"}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_]+", text.lower())
        if len(token) >= 4 and token not in STOPWORDS
    }


def _cluster_for_text(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("pointer", "provenance", "transmute", "raw pointer")):
        return "pointer_provenance_transmute"
    if any(
        token in lowered for token in ("unsafe", "extern", "attribute", "lint", "target_feature")
    ):
        return "unsafe_attribute_extern"
    return "corpus_gap_staleness"


def _classify_item(
    *,
    source_fls_id: str,
    source_exists: bool,
    plausible: bool,
    acceptable_ids: list[str],
    runtime_paragraph_id: str,
    nearest_candidates: list[dict[str, Any]],
    source_overlap_count: int,
) -> str:
    best_candidate_score = max(
        (int(item.get("overlap_count", 0)) for item in nearest_candidates), default=0
    )
    nearest_ids = {str(item.get("paragraph_id", "")) for item in nearest_candidates}
    acceptable_set = {value for value in acceptable_ids if value}
    if not source_fls_id:
        return "stale_mapping" if best_candidate_score >= 3 or acceptable_ids else "corpus_gap"
    if not source_exists:
        return "stale_mapping" if best_candidate_score >= 3 or acceptable_ids else "corpus_gap"
    if acceptable_set and source_fls_id in acceptable_set and plausible:
        return "true_ranking_bug"
    if acceptable_set and nearest_ids & acceptable_set and source_fls_id not in acceptable_set:
        return "weak_mapping"
    if source_overlap_count < best_candidate_score and best_candidate_score >= 3:
        return "weak_mapping"
    if not plausible:
        return "weak_mapping" if best_candidate_score >= 2 else "corpus_gap"
    if runtime_paragraph_id and runtime_paragraph_id.startswith("fls_"):
        return "true_ranking_bug"
    return "true_ranking_bug"


def _paragraph_row(connection: sqlite3.Connection, paragraph_id: str) -> dict[str, str] | None:
    row = connection.execute(
        """
        SELECT paragraph_id, document_link, section_link, clean_text
        FROM paragraphs
        WHERE paragraph_id = ?
        """,
        (paragraph_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "paragraph_id": str(row[0]),
        "document_link": str(row[1]),
        "section_link": str(row[2]),
        "clean_text": str(row[3]),
    }


def _nearest_candidates(
    connection: sqlite3.Connection,
    *,
    guideline_text: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    query_tokens = _tokens(guideline_text)
    if not query_tokens:
        return []
    rows = connection.execute(
        "SELECT paragraph_id, document_link, section_link, clean_text FROM paragraphs"
    ).fetchall()
    scored: list[dict[str, Any]] = []
    for row in rows:
        if str(row[1]) in GENERIC_DOCUMENTS:
            continue
        clean_text = str(row[3])
        overlap = sorted(query_tokens & _tokens(clean_text))
        if not overlap:
            continue
        scored.append(
            {
                "paragraph_id": str(row[0]),
                "document_link": str(row[1]),
                "section_link": str(row[2]),
                "overlap_count": len(overlap),
                "overlap_tokens": overlap,
                "text_excerpt": clean_text[:220],
            }
        )
    scored.sort(key=lambda item: (-int(item["overlap_count"]), str(item["paragraph_id"])))
    return scored[:limit]


def _guideline_path(guidelines_root: Path, *, chapter: str, filename: str) -> Path:
    return guidelines_root / "src" / "coding-guidelines" / chapter / filename


def _heldout_rows(guidelines_root: Path, manifest_path: Path) -> list[dict[str, Any]]:
    payload = _load_json(manifest_path)
    rows = payload.get("items") if isinstance(payload, dict) else []
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        rel_path = str(row.get("path", "")).strip()
        if not rel_path:
            continue
        rst_path = guidelines_root / rel_path
        out.append(
            {
                "source_kind": "heldout_guideline",
                "source_id": str((row.get("provenance") or {}).get("stable_identifier", rel_path)),
                "target_id": str((row.get("provenance") or {}).get("stable_identifier", rel_path)),
                "rst_path": rst_path,
                "title": extract_topic_from_rst(rst_path) if rst_path.exists() else rst_path.stem,
                "acceptable_ids": [
                    str(value).strip()
                    for value in list(row.get("acceptable_ids") or [])
                    if str(value).strip()
                ],
                "runtime_paragraph_id": "",
                "reason_code": "heldout",
                "report_path": "",
                "rationale": str(row.get("rationale", "")).strip(),
            }
        )
    return out


def _blocked_publish_rows(guidelines_root: Path, audit_path: Path) -> list[dict[str, Any]]:
    payload = _load_json(audit_path)
    rows = payload.get("rows") if isinstance(payload, dict) else []
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or bool(row.get("publishable", False)):
            continue
        chapter = str(row.get("chapter", "")).strip()
        filename = str(row.get("filename", "")).strip()
        rst_path = _guideline_path(guidelines_root, chapter=chapter, filename=filename)
        out.append(
            {
                "source_kind": "writer_target",
                "source_id": str(row.get("guideline_id", "")).strip() or filename,
                "target_id": str(row.get("target_id", "")).strip(),
                "rst_path": rst_path,
                "title": str(row.get("title", "")).strip() or rst_path.stem,
                "acceptable_ids": [],
                "runtime_paragraph_id": str(row.get("resolved_paragraph_id", "")).strip(),
                "reason_code": str(row.get("reason_code", "")).strip(),
                "report_path": str(row.get("report_path", "")).strip(),
                "rationale": str(row.get("reason", "")).strip(),
            }
        )
    return out


def generate_mapping_audit(
    *,
    fls_db_path: Path,
    guidelines_root: Path,
    output_path: Path,
    heldout_manifest_path: Path,
    publishability_audit_path: Path,
) -> Path:
    rows = _heldout_rows(guidelines_root, heldout_manifest_path)
    if publishability_audit_path.exists():
        rows.extend(_blocked_publish_rows(guidelines_root, publishability_audit_path))

    connection = sqlite3.connect(fls_db_path)
    try:
        audit_rows: list[dict[str, Any]] = []
        for row in rows:
            rst_path = Path(row["rst_path"])
            source_ids = extract_fls_ids_from_rst(rst_path) if rst_path.exists() else []
            source_fls_id = source_ids[0] if source_ids else ""
            source_row = _paragraph_row(connection, source_fls_id) if source_fls_id else None
            source_exists = source_row is not None
            guideline_text = row["title"]
            if rst_path.exists():
                guideline_text += "\n" + rst_path.read_text(encoding="utf-8")
            nearest_candidates = _nearest_candidates(connection, guideline_text=guideline_text)
            overlap = sorted(
                _tokens(guideline_text) & _tokens(source_row["clean_text"] if source_row else "")
            )
            plausible = len(overlap) >= 2
            classification = _classify_item(
                source_fls_id=source_fls_id,
                source_exists=source_exists,
                plausible=plausible,
                acceptable_ids=list(row.get("acceptable_ids") or []),
                runtime_paragraph_id=str(row.get("runtime_paragraph_id", "")),
                nearest_candidates=nearest_candidates,
                source_overlap_count=len(overlap),
            )
            audit_rows.append(
                {
                    "source_kind": row["source_kind"],
                    "source_id": row["source_id"],
                    "target_id": row["target_id"],
                    "title": row["title"],
                    "rst_path": str(rst_path),
                    "source_fls_id": source_fls_id,
                    "source_fls_exists": source_exists,
                    "source_fls_semantic_plausibility": plausible,
                    "source_fls_overlap_tokens": overlap,
                    "nearest_candidate_paragraphs": nearest_candidates,
                    "best_runtime_paragraph_id": str(row.get("runtime_paragraph_id", "")),
                    "acceptable_ids": list(row.get("acceptable_ids") or []),
                    "classification": classification,
                    "cluster": _cluster_for_text(str(row["title"])),
                    "reason_code": row["reason_code"],
                    "report_path": row["report_path"],
                    "evidence": {
                        "rationale": row["rationale"],
                        "source_document_link": source_row["document_link"] if source_row else "",
                        "source_section_link": source_row["section_link"] if source_row else "",
                    },
                }
            )
    finally:
        connection.close()

    payload = {
        "generated_from": {
            "fls_db_path": str(fls_db_path),
            "guidelines_root": str(guidelines_root),
            "heldout_manifest_path": str(heldout_manifest_path),
            "publishability_audit_path": str(publishability_audit_path),
        },
        "rows": audit_rows,
        "classification_counts": {
            key: sum(1 for row in audit_rows if row["classification"] == key)
            for key in ("stale_mapping", "weak_mapping", "corpus_gap", "true_ranking_bug")
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate WS7 mapping audit artifact")
    parser.add_argument("--fls-db", default=str(DEFAULT_FLS_DB))
    parser.add_argument("--guidelines-root", default=str(DEFAULT_GUIDELINES_ROOT))
    parser.add_argument("--heldout-manifest", default=str(DEFAULT_HELDOUT_MANIFEST))
    parser.add_argument("--publishability-audit", default=str(DEFAULT_PUBLISHABILITY_AUDIT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    args = parser.parse_args()
    path = generate_mapping_audit(
        fls_db_path=Path(args.fls_db).expanduser().resolve(),
        guidelines_root=Path(args.guidelines_root).expanduser().resolve(),
        heldout_manifest_path=Path(args.heldout_manifest).expanduser().resolve(),
        publishability_audit_path=Path(args.publishability_audit).expanduser().resolve(),
        output_path=Path(args.output).expanduser().resolve(),
    )
    print(json.dumps({"output_path": str(path)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
