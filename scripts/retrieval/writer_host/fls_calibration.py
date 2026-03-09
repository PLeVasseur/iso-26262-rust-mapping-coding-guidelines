from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from typing import Any, cast

from context.fls_lookup import resolve_fls_for_guideline
from retrieval.writer_host.fls_grounding import build_grounding_artifact


EXPECTED_GROUNDING_FIELDS = {
    "governing_obligation",
    "construct_terms",
    "code_tokens",
    "supporting_phrases",
    "prior_documents",
    "prior_sections",
    "ambiguity_notes",
}


def extract_fls_ids_from_rst(rst_path: Path) -> list[str]:
    content = rst_path.read_text(encoding="utf-8")
    matches = re.findall(r":fls:`(fls_\w+)`|:fls:\s+(fls_\w+)", content)
    out: list[str] = []
    for left, right in matches:
        value = left or right
        if value and value not in out:
            out.append(value)
    return out


def extract_topic_from_rst(rst_path: Path) -> str:
    lines = rst_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if index + 1 >= len(lines):
            continue
        underline = lines[index + 1].strip()
        if (
            len(underline) >= 3
            and underline == underline[0] * len(underline)
            and underline[0] in "=-~^"
        ):
            return line.strip()
    return ""


def _body_lines_after_title(*, content: str, title: str) -> list[str]:
    body_lines: list[str] = []
    capture = False
    for line in content.splitlines():
        stripped = line.strip()
        if not capture and title and stripped == title:
            capture = True
            continue
        if capture and stripped and set(stripped) <= {"=", "-", "~", "^"}:
            continue
        if capture:
            body_lines.append(line)
    return body_lines


def _extract_rust_example_blocks(lines: list[str]) -> tuple[list[str], list[str]]:
    code_blocks: list[str] = []
    kept_lines: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip().startswith(".. rust-example::"):
            directive_indent = len(line) - len(line.lstrip(" "))
            index += 1
            raw_body: list[str] = []
            while index < len(lines):
                current = lines[index]
                current_indent = len(current) - len(current.lstrip(" "))
                if current.strip() and current_indent <= directive_indent:
                    break
                raw_body.append(current)
                index += 1
            while raw_body and not raw_body[0].strip():
                raw_body.pop(0)
            while raw_body and raw_body[0].lstrip().startswith(":"):
                raw_body.pop(0)
            while raw_body and not raw_body[0].strip():
                raw_body.pop(0)
            code = textwrap.dedent("\n".join(raw_body)).strip()
            if code:
                code_blocks.append(code)
            continue
        kept_lines.append(line)
        index += 1
    return kept_lines, code_blocks


def _prose_before_example_sections(lines: list[str]) -> list[str]:
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(".. non_compliant_example::") or stripped.startswith(
            ".. compliant_example::"
        ):
            break
        kept.append(line)
    return kept


def _normalize_prose_block(block: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(".. ") or line.startswith(":"):
            continue
        line = re.sub(r"^[|*\-]+\s*", "", line)
        line = line.replace("**", "").replace("``", "")
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            cleaned_lines.append(line)
    return " ".join(cleaned_lines).strip()


def build_resolution_packet_from_rst(rst_path: Path) -> dict[str, Any]:
    content = rst_path.read_text(encoding="utf-8")
    title = extract_topic_from_rst(rst_path)
    body_lines = _body_lines_after_title(content=content, title=title)
    prose_lines, code_blocks = _extract_rust_example_blocks(body_lines)
    prose_lines = _prose_before_example_sections(prose_lines)
    prose_blocks = [
        block.strip() for block in re.split(r"\n\s*\n", "\n".join(prose_lines)) if block.strip()
    ]
    cleaned_blocks: list[str] = []
    for block in prose_blocks:
        cleaned = _normalize_prose_block(block)
        if not cleaned:
            continue
        cleaned_blocks.append(cleaned)
    amplification_text = cleaned_blocks[0] if cleaned_blocks else ""
    rationale_text = " ".join(cleaned_blocks[1:])
    aggregated_code = "\n\n".join(block.strip() for block in code_blocks if block.strip())
    return build_grounding_artifact(
        {
            "draft": {
                "target_id": rst_path.stem,
                "title": title,
                "construct_terms": [],
                "claim_to_evidence_map": [{"claim_text": title}] if title else [],
            },
            "amplification": {"guideline_amplification_text": amplification_text},
            "rationale": {"rationale_text": rationale_text},
            "examples": {
                "non_compliant_code": aggregated_code,
                "compliant_code": "",
            },
            "metadata": {},
        }
    )


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _assert_runtime_allowed_dataset(dataset_path: Path) -> None:
    payload = _load_json(dataset_path)
    if bool(payload.get("runtime_use_prohibited", False)):
        raise RuntimeError(
            f"dataset {dataset_path} is marked runtime_use_prohibited and cannot be used for calibration"
        )


def load_calibration_items(
    *,
    manifest_path: Path,
    guidelines_repo_root: Path,
    dataset_path: Path | None = None,
) -> list[dict[str, Any]]:
    if dataset_path is not None and dataset_path.exists():
        _assert_runtime_allowed_dataset(dataset_path)
        payload = _load_json(dataset_path)
        rows = payload.get("items") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            rows = []
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            path_raw = str(row.get("path", "")).strip()
            if not path_raw:
                continue
            rst_path = (guidelines_repo_root / path_raw).resolve()
            if not rst_path.exists():
                continue
            acceptable_ids = [
                str(value).strip()
                for value in list(row.get("acceptable_ids") or [])
                if str(value).strip()
            ]
            acceptable_chapters = [
                str(value).strip()
                for value in list(row.get("acceptable_chapters") or [])
                if str(value).strip()
            ]
            out.append(
                {
                    "path": path_raw,
                    "rst_path": rst_path,
                    "packet": build_resolution_packet_from_rst(rst_path),
                    "acceptable_ids": acceptable_ids,
                    "acceptable_chapters": acceptable_chapters,
                    "should_abstain": bool(row.get("should_abstain", False)),
                }
            )
        return out

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("exemplars") if isinstance(manifest, dict) else []
    if not isinstance(entries, list):
        entries = []
    out: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rel = str(entry.get("path", "")).strip()
        if not rel:
            continue
        rst_path = guidelines_repo_root / rel
        if not rst_path.exists():
            continue
        ids = extract_fls_ids_from_rst(rst_path)
        if not ids:
            continue
        out.append(
            {
                "path": rel,
                "rst_path": rst_path,
                "packet": build_resolution_packet_from_rst(rst_path),
                "acceptable_ids": ids,
                "acceptable_chapters": [],
                "should_abstain": False,
            }
        )
    return out


def evaluate_calibration_items(
    *,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    total = 0
    ws7_required = 0
    grounding_only_runtime = 0
    structurally_valid = 0
    no_legacy_fields = 0
    abstention_correct = 0
    publish_accept_violations = 0
    rows: list[dict[str, Any]] = []

    for item in items:
        raw_packet = item.get("packet")
        packet: dict[str, Any] = (
            cast(dict[str, Any], dict(raw_packet)) if isinstance(raw_packet, dict) else {}
        )
        acceptable_ids = [
            str(value).strip()
            for value in list(item.get("acceptable_ids") or [])
            if str(value).strip()
        ]
        packet_fields_exact = set(packet) == EXPECTED_GROUNDING_FIELDS
        packet_has_no_legacy = all(
            key not in packet for key in ("expected_domains", "field_terms", "code_symbols")
        )

        try:
            predicted: dict[str, Any] = resolve_fls_for_guideline(
                packet,
            )
        except RuntimeError:
            predicted = {
                "paragraph_id": "fls_UNRESOLVED",
                "decision": {"reason_code": "RUNTIME_ERROR"},
            }

        raw_decision = predicted.get("decision")
        decision: dict[str, Any] = (
            cast(dict[str, Any], dict(raw_decision)) if isinstance(raw_decision, dict) else {}
        )
        predicted_id = str(predicted.get("paragraph_id", "fls_UNRESOLVED"))
        reason_code = str(decision.get("reason_code", ""))
        is_grounding_only = bool(decision.get("grounding_only_runtime", False))
        publish_accept = bool(decision.get("publish_accept", False))

        total += 1
        if packet_fields_exact:
            structurally_valid += 1
        if packet_has_no_legacy:
            no_legacy_fields += 1
        if reason_code == "WS7_REQUIRED":
            ws7_required += 1
        if is_grounding_only:
            grounding_only_runtime += 1
        if (
            predicted_id == "fls_UNRESOLVED"
            and reason_code == "WS7_REQUIRED"
            and not publish_accept
        ):
            abstention_correct += 1
        if publish_accept:
            publish_accept_violations += 1

        rows.append(
            {
                "path": str(item.get("path", "")),
                "predicted_id": predicted_id,
                "acceptable_ids": acceptable_ids,
                "packet_fields_exact": packet_fields_exact,
                "packet_has_no_legacy_fields": packet_has_no_legacy,
                "reason_code": reason_code,
                "publish_accept": publish_accept,
                "grounding_only_runtime": is_grounding_only,
            }
        )

    return {
        "total": total,
        "ws7_required": ws7_required,
        "grounding_only_runtime": grounding_only_runtime,
        "structurally_valid": structurally_valid,
        "no_legacy_fields": no_legacy_fields,
        "abstention_correct": abstention_correct,
        "publish_accept_violations": publish_accept_violations,
        "ws7_required_ratio": (ws7_required / total) if total else 0.0,
        "grounding_only_ratio": (grounding_only_runtime / total) if total else 0.0,
        "structurally_valid_ratio": (structurally_valid / total) if total else 0.0,
        "no_legacy_fields_ratio": (no_legacy_fields / total) if total else 0.0,
        "abstention_correct_ratio": (abstention_correct / total) if total else 0.0,
        "rows": rows,
    }


def run_threshold_sweep(
    *,
    items: list[dict[str, Any]],
    base_policy: dict[str, Any],
) -> dict[str, Any]:
    del items, base_policy
    raise RuntimeError(
        "WS7_REQUIRED: threshold sweep is disabled while runtime remains grounding-only"
    )


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(dict(out[key]), value)
        else:
            out[key] = value
    return out
