from __future__ import annotations

from typing import Any


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _low(value: Any) -> str:
    return _clean(value).lower()


def evaluate_evidence_quality(
    *,
    target_id: str,
    query_text: str,
    synth: dict[str, Any],
    metadata: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    issues: list[str] = []
    severity = "pass"
    metadata_notes = metadata.get("metadata_validation_notes") if isinstance(metadata, dict) else []
    notes = [str(value) for value in metadata_notes] if isinstance(metadata_notes, list) else []
    notes_blob = " ".join(notes).lower()
    evidence_text = " ".join(
        _low(row.get("statement_text")) for row in evidence_rows if isinstance(row, dict)
    )
    query_blob = _low(query_text)
    construct_scope = synth.get("construct_scope") if isinstance(synth, dict) else []
    constructs = (
        " ".join(_low(value) for value in construct_scope)
        if isinstance(construct_scope, list)
        else ""
    )

    if "off-target" in notes_blob or "mismatch" in notes_blob:
        issues.append("off_target_evidence")
        severity = "fail"
    if target_id.startswith("RET-NEG-"):
        issues.append("negative_control_target")
        severity = "fail"
    if query_blob and "nohits" in query_blob:
        issues.append("query_nohit_style")
        severity = "fail"
    if (
        constructs
        and "atomic" in constructs
        and " not " in f" {evidence_text} "
        and "atomic" not in evidence_text
    ):
        issues.append("construct_evidence_mismatch")
        severity = "fail"
    if (
        constructs
        and "pin" in constructs
        and "pin" not in evidence_text
        and "move" not in evidence_text
    ):
        issues.append("weak_pin_evidence_alignment")
        severity = "review" if severity == "pass" else severity
    if not evidence_rows:
        issues.append("empty_evidence_rows")
        severity = "fail"

    return {
        "target_id": target_id,
        "status": severity,
        "issues": sorted(dict.fromkeys(issues)),
        "blocked": severity == "fail",
    }
