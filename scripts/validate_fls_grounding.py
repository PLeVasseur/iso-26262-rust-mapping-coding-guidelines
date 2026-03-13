"""Produce WS6 grounding-validation artifacts."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from context.exemplars import EXEMPLAR_MANIFEST, GUIDELINES_REPO_ROOT
    from retrieval.writer_host.fls_calibration import build_resolution_packet_from_rst
except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(PROJECT_ROOT))
    from context.exemplars import EXEMPLAR_MANIFEST, GUIDELINES_REPO_ROOT
    from retrieval.writer_host.fls_calibration import build_resolution_packet_from_rst


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HELDOUT_MANIFEST = PROJECT_ROOT / "data" / "fls_grounding_heldout_manifest.json"
DEFAULT_OUTPUT = (
    PROJECT_ROOT / ".cache" / "sqlite_kb" / "reports" / "fls_spec" / "ws6_grounding_validation.json"
)
GENERIC_FAMILY_TOKENS = {
    "attribute",
    "attributes",
    "call",
    "calls",
    "concept",
    "concepts",
    "expression",
    "expressions",
    "function",
    "functions",
    "item",
    "items",
    "trait",
    "traits",
    "type",
    "types",
}


def _load_manifest_entries(manifest_path: Path, key: str) -> list[dict[str, Any]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = payload.get(key) if isinstance(payload, dict) else []
    return [row for row in list(rows or []) if isinstance(row, dict)]


def _load_manifest_payload(manifest_path: Path) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _family_tokens(value: str) -> set[str]:
    normalized = value.lower().replace("/", " ").replace("-", " ").replace("_", " ").strip()
    return {token for token in normalized.split() if token}


def _family_alternatives(value: str) -> list[str]:
    text = str(value or "").strip()
    return [text] if text else []


def _strong_phrase_match(expected_phrase: str, seen_tokens: set[str]) -> dict[str, Any]:
    tokens = _family_tokens(expected_phrase)
    matched = tokens & seen_tokens
    specific_tokens = tokens - GENERIC_FAMILY_TOKENS
    matched_specific = matched - GENERIC_FAMILY_TOKENS
    if not tokens:
        return {"matched": True, "expected_tokens": [], "matched_tokens": []}
    if len(tokens) == 1:
        token = next(iter(tokens))
        matched_ok = token in seen_tokens
        return {
            "matched": matched_ok,
            "expected_tokens": sorted(tokens),
            "matched_tokens": sorted(matched),
        }
    if specific_tokens:
        matched_ok = len(matched_specific) >= min(2, len(specific_tokens))
    else:
        matched_ok = len(matched) >= 2
    return {
        "matched": matched_ok,
        "expected_tokens": sorted(tokens),
        "matched_tokens": sorted(matched),
    }


def _expectation_match(expected_value: str, seen_tokens: set[str]) -> dict[str, Any]:
    alternatives = _family_alternatives(expected_value)
    if not alternatives:
        return {
            "matched": True,
            "matched_alternative": "",
            "expected_alternatives": [],
            "alternative_results": [],
        }
    results = []
    for alternative in alternatives:
        result = _strong_phrase_match(alternative, seen_tokens)
        results.append({"phrase": alternative, **result})
        if result["matched"]:
            return {
                "matched": True,
                "matched_alternative": alternative,
                "expected_alternatives": alternatives,
                "alternative_results": results,
            }
    return {
        "matched": False,
        "matched_alternative": "",
        "expected_alternatives": alternatives,
        "alternative_results": results,
    }


def _artifact_neighborhood_tokens(artifact: dict[str, Any]) -> dict[str, set[str]]:
    document_tokens: set[str] = set()
    section_tokens: set[str] = set()

    def _evidence_tokens(value: Any) -> set[str]:
        if isinstance(value, str):
            return _family_tokens(value)
        if isinstance(value, dict):
            out: set[str] = set()
            for nested in value.values():
                out.update(_evidence_tokens(nested))
            return out
        if isinstance(value, list):
            out: set[str] = set()
            for nested in value:
                out.update(_evidence_tokens(nested))
            return out
        return set()

    for row in list(artifact.get("prior_documents") or []):
        if not isinstance(row, dict):
            continue
        link = str(row.get("document_link", "")).strip()
        stem = Path(link.split("#", 1)[0]).stem
        document_tokens.update(_family_tokens(stem))
        document_tokens.update(_family_tokens(str(row.get("content_type", ""))))
        evidence_raw = row.get("evidence")
        evidence: dict[str, Any] = evidence_raw if isinstance(evidence_raw, dict) else {}
        document_tokens.update(_evidence_tokens(evidence.get("heading_hits")))
        document_tokens.update(_evidence_tokens(evidence.get("phrase_hits")))
    for row in list(artifact.get("prior_sections") or []):
        if not isinstance(row, dict):
            continue
        link = str(row.get("section_link", "")).strip()
        doc_part, _, anchor = link.partition("#")
        document_tokens.update(_family_tokens(Path(doc_part).stem))
        section_tokens.update(_family_tokens(anchor))
        section_tokens.update(_family_tokens(str(row.get("content_type", ""))))
        evidence_raw = row.get("evidence")
        evidence = evidence_raw if isinstance(evidence_raw, dict) else {}
        document_tokens.update(_evidence_tokens(evidence.get("heading_hits")))
        section_tokens.update(_evidence_tokens(evidence.get("heading_hits")))
        section_tokens.update(_evidence_tokens(evidence.get("phrase_hits")))
    return {
        "document_family_tokens": document_tokens,
        "section_family_tokens": section_tokens,
    }


def _semantic_expectation_checks(
    *, artifact: dict[str, Any], expected_neighborhood: dict[str, Any]
) -> dict[str, Any]:
    if not expected_neighborhood:
        return {
            "has_expectations": False,
            "document_family_match": True,
            "section_family_match": True,
            "document_family_expected": "",
            "section_family_expected": "",
            "document_family_tokens_seen": [],
            "section_family_tokens_seen": [],
        }
    seen = _artifact_neighborhood_tokens(artifact)
    expected_document = str(expected_neighborhood.get("document_family", "")).strip()
    expected_section = str(expected_neighborhood.get("section_family", "")).strip()
    seen_document = seen["document_family_tokens"]
    seen_section = seen["section_family_tokens"]
    document_result = _expectation_match(expected_document, seen_document)
    section_result = _expectation_match(expected_section, seen_section)
    return {
        "has_expectations": True,
        "document_family_match": bool(document_result["matched"]),
        "section_family_match": bool(section_result["matched"]),
        "document_family_expected": expected_document,
        "section_family_expected": expected_section,
        "document_family_matched_alternative": str(document_result["matched_alternative"]),
        "section_family_matched_alternative": str(section_result["matched_alternative"]),
        "document_family_alternatives": list(document_result["expected_alternatives"]),
        "section_family_alternatives": list(section_result["expected_alternatives"]),
        "document_family_alternative_results": list(document_result["alternative_results"]),
        "section_family_alternative_results": list(section_result["alternative_results"]),
        "document_family_tokens_seen": sorted(seen_document),
        "section_family_tokens_seen": sorted(seen_section),
    }


def _artifact_report(
    *,
    label: str,
    rst_path: Path,
    expected_neighborhood: dict[str, Any] | None = None,
    revision_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact = build_resolution_packet_from_rst(rst_path)
    prior_documents = list(artifact.get("prior_documents") or [])
    prior_sections = list(artifact.get("prior_sections") or [])
    semantic_checks = _semantic_expectation_checks(
        artifact=artifact,
        expected_neighborhood=expected_neighborhood
        if isinstance(expected_neighborhood, dict)
        else {},
    )
    return {
        "label": label,
        "path": str(rst_path),
        "revision_provenance": dict(revision_provenance or {}),
        "artifact": artifact,
        "checks": {
            "top_level_fields_exact": set(artifact)
            == {
                "governing_obligation",
                "construct_terms",
                "code_tokens",
                "supporting_phrases",
                "prior_documents",
                "prior_sections",
                "ambiguity_notes",
            },
            "no_legacy_fields": all(
                key not in artifact for key in ("expected_domains", "field_terms", "code_symbols")
            ),
            "priors_have_only_allowed_keys": all(
                set(row)
                == {"document_link", "score", "content_type", "specificity_state", "evidence"}
                for row in prior_documents
            )
            and all(
                set(row)
                == {"section_link", "score", "content_type", "specificity_state", "evidence"}
                for row in prior_sections
            ),
            "priors_do_not_expose_candidate_ids": all(
                "paragraph_id" not in row and "paragraph_link" not in row and "chunk_uid" not in row
                for row in prior_documents + prior_sections
            ),
            "weak_evidence_noted": any(
                note in {"broad_document_priors", "broad_section_priors"}
                for note in list(artifact.get("ambiguity_notes") or [])
            )
            or bool(prior_documents)
            or bool(prior_sections),
            "semantic_expectations_checked": bool(semantic_checks.get("has_expectations", False)),
            "document_family_match": bool(semantic_checks.get("document_family_match", False)),
            "section_family_match": bool(semantic_checks.get("section_family_match", False)),
        },
        "semantic_expectations": semantic_checks,
    }


def run_grounding_validation(
    *,
    guidelines_repo_root: Path = GUIDELINES_REPO_ROOT,
    heldout_manifest_path: Path = HELDOUT_MANIFEST,
    exemplar_manifest_path: Path = EXEMPLAR_MANIFEST,
    output_path: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    heldout_entries = _load_manifest_entries(heldout_manifest_path, "entries")
    heldout_manifest_payload = _load_manifest_payload(heldout_manifest_path)
    exemplar_entries = _load_manifest_entries(exemplar_manifest_path, "exemplars")
    reports: list[dict[str, Any]] = []

    for entry in heldout_entries:
        raw_path = str(entry.get("source_path", "")).strip()
        if not raw_path:
            continue
        rst_path = (guidelines_repo_root / raw_path).resolve()
        if not rst_path.exists():
            continue
        reports.append(
            _artifact_report(
                label=str(entry.get("stable_identifier", raw_path)),
                rst_path=rst_path,
                expected_neighborhood=(
                    entry.get("expected_fls_neighborhood_type")
                    if isinstance(entry.get("expected_fls_neighborhood_type"), dict)
                    else {}
                ),
                revision_provenance=(
                    entry.get("revision_provenance")
                    if isinstance(entry.get("revision_provenance"), dict)
                    else {}
                ),
            )
        )

    for entry in exemplar_entries:
        raw_path = str(entry.get("path", "")).strip()
        if not raw_path:
            continue
        rst_path = (guidelines_repo_root / raw_path).resolve()
        if not rst_path.exists():
            continue
        reports.append(_artifact_report(label=f"exemplar::{raw_path}", rst_path=rst_path))

    summary = {
        "runtime_mode": "grounding_only_ws6",
        "guidelines_repo_root": str(guidelines_repo_root),
        "heldout_manifest": str(heldout_manifest_path),
        "heldout_manifest_status": str(heldout_manifest_payload.get("status", "")),
        "heldout_manifest_revision_history": list(
            heldout_manifest_payload.get("revision_history") or []
        ),
        "heldout_manifest_has_post_freeze_revisions": bool(
            heldout_manifest_payload.get("revision_history")
        ),
        "exemplar_manifest": str(exemplar_manifest_path),
        "artifact_count": len(reports),
        "all_top_level_fields_exact": all(
            bool(item["checks"].get("top_level_fields_exact", False)) for item in reports
        ),
        "all_no_legacy_fields": all(
            bool(item["checks"].get("no_legacy_fields", False)) for item in reports
        ),
        "all_priors_have_only_allowed_keys": all(
            bool(item["checks"].get("priors_have_only_allowed_keys", False)) for item in reports
        ),
        "all_priors_do_not_expose_candidate_ids": all(
            bool(item["checks"].get("priors_do_not_expose_candidate_ids", False))
            for item in reports
        ),
        "all_semantic_expectations_satisfied": all(
            bool(item["checks"].get("document_family_match", False))
            and bool(item["checks"].get("section_family_match", False))
            for item in reports
            if bool(item["checks"].get("semantic_expectations_checked", False))
        ),
    }
    payload = {
        "summary": summary,
        "artifacts": reports,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    payload = run_grounding_validation(
        guidelines_repo_root=Path(os.environ.get("GUIDELINES_REPO", str(GUIDELINES_REPO_ROOT)))
    )
    summary_raw = payload.get("summary")
    summary: dict[str, Any] = summary_raw if isinstance(summary_raw, dict) else {}
    print("WS6 Grounding Validation")
    print(f"  Artifacts: {int(summary.get('artifact_count', 0) or 0)}")
    print(f"  Exact fields: {bool(summary.get('all_top_level_fields_exact', False))}")
    print(f"  No legacy fields: {bool(summary.get('all_no_legacy_fields', False))}")
    print(
        f"  Prior key discipline: {bool(summary.get('all_priors_have_only_allowed_keys', False))}"
    )
    print(
        "  No candidate-id priors: "
        f"{bool(summary.get('all_priors_do_not_expose_candidate_ids', False))}"
    )
    print(
        "  Semantic expectations: "
        f"{bool(summary.get('all_semantic_expectations_satisfied', False))}"
    )
    print(f"Report saved: {DEFAULT_OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
