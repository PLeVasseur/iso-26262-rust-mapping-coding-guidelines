from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from context.fls_lookup import resolve_fls_for_guideline


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


def build_resolution_packet_from_rst(rst_path: Path) -> dict[str, Any]:
    content = rst_path.read_text(encoding="utf-8")
    title = extract_topic_from_rst(rst_path)
    tags_match = re.search(r":tags:\s+(.+)", content)
    tags = []
    if tags_match:
        tags = [value.strip().lower() for value in tags_match.group(1).split(",") if value.strip()]
    code_blocks = re.findall(r"\.\. rust-example::\n(?:\s+:[^\n]+\n)*\n((?:\n|\s+.+\n)+)", content)
    non_compliant_code = code_blocks[0].strip() if code_blocks else ""
    compliant_code = code_blocks[1].strip() if len(code_blocks) > 1 else ""
    return {
        "target_id": rst_path.stem,
        "title": title,
        "construct_terms": title.split(),
        "expected_domains": tags,
        "amplification_text": content,
        "rationale_text": content,
        "non_compliant_narrative": content,
        "non_compliant_code": non_compliant_code,
        "compliant_narrative": content,
        "compliant_code": compliant_code,
        "claim_phrases": [title],
        "min_variant_coverage": 1,
    }


def load_calibration_items(
    *,
    manifest_path: Path,
    guidelines_repo_root: Path,
    dataset_path: Path | None = None,
) -> list[dict[str, Any]]:
    if dataset_path is not None and dataset_path.exists():
        payload = json.loads(dataset_path.read_text(encoding="utf-8"))
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
    policy_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    total = 0
    strict_top1 = 0
    topk_match = 0
    chapter_match = 0
    unresolved = 0
    false_accept = 0
    false_reject = 0
    rows: list[dict[str, Any]] = []

    for item in items:
        packet = item.get("packet") if isinstance(item.get("packet"), dict) else {}
        acceptable_ids = [
            str(value).strip()
            for value in list(item.get("acceptable_ids") or [])
            if str(value).strip()
        ]
        acceptable_chapters = {
            str(value).strip()
            for value in list(item.get("acceptable_chapters") or [])
            if str(value).strip()
        }
        should_abstain = bool(item.get("should_abstain", False))

        try:
            predicted = resolve_fls_for_guideline(packet, policy_overrides=policy_overrides)
        except RuntimeError:
            predicted = {
                "paragraph_id": "fls_UNRESOLVED",
                "decision": {"reason_code": "RUNTIME_ERROR"},
            }

        decision = predicted.get("decision") if isinstance(predicted.get("decision"), dict) else {}
        predicted_id = str(predicted.get("paragraph_id", "fls_UNRESOLVED"))
        predicted_chapter = str(predicted.get("chapter", ""))
        top_candidates = list(decision.get("top_candidates") or [])
        top_candidate_ids = [
            str(row.get("paragraph_id", "")).strip()
            for row in top_candidates
            if isinstance(row, dict) and str(row.get("paragraph_id", "")).strip()
        ]

        total += 1
        if predicted_id == "fls_UNRESOLVED":
            unresolved += 1

        strict_match = bool(acceptable_ids) and predicted_id in acceptable_ids
        if strict_match:
            strict_top1 += 1

        topk_contains = bool(acceptable_ids) and any(
            value in acceptable_ids for value in top_candidate_ids
        )
        if topk_contains:
            topk_match += 1

        chapter_ok = bool(acceptable_chapters) and predicted_chapter in acceptable_chapters
        if chapter_ok:
            chapter_match += 1

        publish_accept = bool(decision.get("publish_accept", False))
        if should_abstain and publish_accept:
            false_accept += 1
        if (not should_abstain) and (predicted_id == "fls_UNRESOLVED"):
            false_reject += 1

        rows.append(
            {
                "path": str(item.get("path", "")),
                "predicted_id": predicted_id,
                "predicted_chapter": predicted_chapter,
                "acceptable_ids": acceptable_ids,
                "acceptable_chapters": sorted(acceptable_chapters),
                "should_abstain": should_abstain,
                "strict_top1_match": strict_match,
                "topk_contains_match": topk_contains,
                "chapter_match": chapter_ok,
                "reason_code": str(decision.get("reason_code", "")),
                "publish_accept": publish_accept,
                "review_candidate": bool(decision.get("review_candidate", False)),
            }
        )

    return {
        "total": total,
        "strict_top1": strict_top1,
        "topk_contains": topk_match,
        "chapter_match": chapter_match,
        "unresolved": unresolved,
        "false_accept": false_accept,
        "false_reject": false_reject,
        "strict_top1_ratio": (strict_top1 / total) if total else 0.0,
        "topk_ratio": (topk_match / total) if total else 0.0,
        "chapter_ratio": (chapter_match / total) if total else 0.0,
        "unresolved_ratio": (unresolved / total) if total else 0.0,
        "rows": rows,
    }


def run_threshold_sweep(
    *,
    items: list[dict[str, Any]],
    base_policy: dict[str, Any],
) -> dict[str, Any]:
    scores = [0.44, 0.48, 0.52]
    overlaps = [0.10, 0.12, 0.14]
    margins = [0.03, 0.05]
    summaries: list[dict[str, Any]] = []
    for score in scores:
        for overlap in overlaps:
            for margin in margins:
                override = {
                    "thresholds": {
                        "min_confidence_score": score,
                        "min_weighted_overlap": overlap,
                        "min_confidence_margin": margin,
                    }
                }
                merged = {key: value for key, value in base_policy.items()}
                merged = _deep_merge(merged, override)
                report = evaluate_calibration_items(items=items, policy_overrides=merged)
                summaries.append(
                    {
                        "thresholds": override["thresholds"],
                        "metrics": {
                            "strict_top1_ratio": report["strict_top1_ratio"],
                            "topk_ratio": report["topk_ratio"],
                            "unresolved_ratio": report["unresolved_ratio"],
                            "false_accept": report["false_accept"],
                        },
                    }
                )
    summaries.sort(
        key=lambda row: (
            int(row["metrics"].get("false_accept", 0)),
            -float(row["metrics"].get("topk_ratio", 0.0)),
            -float(row["metrics"].get("strict_top1_ratio", 0.0)),
            float(row["metrics"].get("unresolved_ratio", 1.0)),
        )
    )
    return {
        "candidate_count": len(summaries),
        "best": summaries[0] if summaries else {},
        "candidates": summaries,
    }


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(dict(out[key]), value)
        else:
            out[key] = value
    return out
