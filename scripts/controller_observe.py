from __future__ import annotations

import csv
import re
import sys
from pathlib import Path
from typing import Any

from _common import read_json, read_yaml, run_command, write_json

GATE_SPECS: list[tuple[str, list[str]]] = [
    (
        "validate_schemas",
        ["scripts/validate_schemas.py", "--strict-generated"],
    ),
    (
        "check_guideline_completeness",
        ["scripts/check_guideline_completeness.py", "--require-fls-refs"],
    ),
    (
        "check_traceability",
        ["scripts/check_traceability.py"],
    ),
    (
        "check_rule_decomposition",
        ["scripts/check_rule_decomposition.py"],
    ),
    (
        "check_fls_proxy_coverage",
        ["scripts/check_fls_proxy_coverage.py"],
    ),
    (
        "check_guideline_quality",
        ["scripts/check_guideline_quality.py"],
    ),
    (
        "check_guideline_examples",
        ["scripts/check_guideline_examples.py"],
    ),
    (
        "check_known_good_alignment",
        ["scripts/check_known_good_alignment.py", "--allow-missing-benchmark"],
    ),
]

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def known_good_override_args(overrides: dict[str, Any] | None) -> list[str]:
    if not overrides:
        return []

    args: list[str] = []
    value = overrides.get("min_global_alignment")
    if value is not None:
        args.extend(["--min-global-alignment", f"{float(value):.6f}"])

    value = overrides.get("min_changed_guideline_alignment")
    if value is not None:
        args.extend(["--min-changed-guideline-alignment", f"{float(value):.6f}"])

    value = overrides.get("granularity_outliers_allowed")
    if value is not None:
        args.extend(["--granularity-outliers-allowed", str(int(value))])

    gate_mode = str(overrides.get("gate_mode") or "").strip()
    if gate_mode in {"warn", "error"}:
        args.extend(["--gate-mode", gate_mode])

    return args


def run_gates(
    root: Path,
    output_dir: Path,
    known_good_alignment_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    reports: dict[str, Any] = {}
    runtime_failures = 0
    policy_failures = 0

    for gate_name, script_args in GATE_SPECS:
        report_path = output_dir / f"{gate_name}.report.json"
        command = [sys.executable, *script_args, "--json-output", str(report_path)]
        if gate_name == "check_known_good_alignment":
            command.extend(known_good_override_args(known_good_alignment_overrides))
        completed = run_command(command, cwd=root)

        if completed.returncode == 3:
            runtime_failures += 1
        elif completed.returncode == 2:
            policy_failures += 1

        report_payload: dict[str, Any] = {}
        if report_path.exists():
            report_payload = read_json(report_path)

        reports[gate_name] = {
            "return_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "report": report_payload,
        }

    reports["_summary"] = {
        "runtime_failures": runtime_failures,
        "policy_failures": policy_failures,
    }
    return reports


def load_iso_obligation_sets(root: Path) -> tuple[set[str], set[str]]:
    seed_payload = read_yaml(root / "data/seed_topics.yaml") or {}
    coverage_path = root / "data/coverage_matrix.csv"

    in_scope_obligations = {
        str(item.get("obligation_unit_id") or "").strip()
        for item in seed_payload.get("seed_topics", [])
        if str(item.get("obligation_unit_id") or "").strip()
    }

    covered_obligations: set[str] = set()
    if coverage_path.exists():
        with coverage_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                obligation = str(row.get("obligation_unit_id") or "").strip()
                if obligation:
                    covered_obligations.add(obligation)

    return in_scope_obligations, covered_obligations


def collect_placeholder_gaps(root: Path) -> list[dict[str, Any]]:
    policy = read_yaml(root / "config/completeness_policy.yaml") or {}
    terms = [
        str(term).strip().lower()
        for term in (policy.get("quality") or {}).get("placeholder_terms", [])
        if str(term).strip()
    ]
    if not terms:
        terms = ["placeholder", "pending", "todo"]

    payload = read_yaml(root / "data/todo_guidelines.yaml") or {}
    deficits: list[dict[str, Any]] = []
    for guideline in payload.get("guidelines", []):
        guideline_id = str(guideline.get("id") or "").strip()
        if not guideline_id:
            continue
        fields = [
            "rule_statement",
            "amplification",
            "exceptions",
            "rationale",
            "decidability_rationale",
        ]
        for field in fields:
            text = str(guideline.get(field) or "")
            lowered = text.lower()
            if any(term in lowered for term in terms):
                deficits.append(
                    {
                        "deficit_id": f"placeholder:{guideline_id}:{field}",
                        "type": "placeholder_gap",
                        "severity": "medium",
                        "guideline_id": guideline_id,
                        "target_id": "",
                        "obligation_unit_id": "",
                        "distance_to_pass": 1,
                        "evidence_ref": f"todo_guidelines.yaml:{guideline_id}:{field}",
                        "details": f"placeholder-term in {field}",
                    }
                )

        examples = guideline.get("examples") or {}
        for side in ["compliant", "non_compliant"]:
            doc_path = str((examples.get(side) or {}).get("doc_path") or "").strip()
            if not doc_path:
                continue
            absolute_path = root / doc_path
            if not absolute_path.exists():
                continue
            markdown = absolute_path.read_text(encoding="utf-8")
            lowered_md = markdown.lower()
            if any(term in lowered_md for term in terms):
                deficits.append(
                    {
                        "deficit_id": f"placeholder:{guideline_id}:examples.{side}",
                        "type": "placeholder_gap",
                        "severity": "medium",
                        "guideline_id": guideline_id,
                        "target_id": "",
                        "obligation_unit_id": "",
                        "distance_to_pass": 1,
                        "evidence_ref": f"{doc_path}",
                        "details": f"placeholder-term in {side} example markdown",
                    }
                )

    return deficits


def collect_traceability_deficits(report: dict[str, Any]) -> list[dict[str, Any]]:
    deficits: list[dict[str, Any]] = []
    for message in report.get("errors", []):
        text = str(message)
        deficit_type = "traceability_gap"
        severity = "critical"
        if "unknown guideline_id" in text:
            severity = "high"
        deficits.append(
            {
                "deficit_id": f"traceability:{len(deficits) + 1}",
                "type": deficit_type,
                "severity": severity,
                "guideline_id": "",
                "target_id": "",
                "obligation_unit_id": "",
                "distance_to_pass": 1,
                "evidence_ref": "check_traceability",
                "details": text,
            }
        )
    return deficits


def collect_decomposition_deficits(report: dict[str, Any]) -> list[dict[str, Any]]:
    deficits: list[dict[str, Any]] = []
    for item in report.get("targets", []):
        if item.get("ok", False):
            continue
        target_id = str(item.get("target_id") or "")
        expected = int(item.get("expected_min", 0))
        actual = int(item.get("actual", 0))
        deficits.append(
            {
                "deficit_id": f"fanout:{target_id}",
                "type": "target_fanout_gap",
                "severity": "high",
                "guideline_id": "",
                "target_id": target_id,
                "obligation_unit_id": "",
                "distance_to_pass": max(0, expected - actual),
                "evidence_ref": "check_rule_decomposition",
                "details": f"fanout {actual} < {expected}",
            }
        )
    return deficits


def collect_fls_deficits(report: dict[str, Any]) -> list[dict[str, Any]]:
    deficits: list[dict[str, Any]] = []
    for item in report.get("targets", []):
        if item.get("ok", False):
            continue
        target_id = str(item.get("target_id") or "")
        threshold = float(item.get("threshold", 0.0))
        ratio = float(item.get("ratio", 0.0))
        deficits.append(
            {
                "deficit_id": f"fls-span:{target_id}",
                "type": "fls_span_gap",
                "severity": "high",
                "guideline_id": "",
                "target_id": target_id,
                "obligation_unit_id": "",
                "distance_to_pass": round(max(0.0, threshold - ratio), 4),
                "evidence_ref": "check_fls_proxy_coverage",
                "details": f"fls span {ratio:.3f} < {threshold:.3f}",
            }
        )

    chapter = report.get("chapter_coverage") or {}
    if chapter and not chapter.get("ok", False):
        ratio = float(chapter.get("ratio", 0.0))
        threshold = float(chapter.get("threshold", 0.0))
        deficits.append(
            {
                "deficit_id": "fls-chapter:global",
                "type": "fls_chapter_gap",
                "severity": "medium",
                "guideline_id": "",
                "target_id": "",
                "obligation_unit_id": "",
                "distance_to_pass": round(max(0.0, threshold - ratio), 4),
                "evidence_ref": "check_fls_proxy_coverage",
                "details": f"chapter coverage {ratio:.3f} < {threshold:.3f}",
            }
        )
    return deficits


def collect_quality_deficits(report: dict[str, Any]) -> list[dict[str, Any]]:
    deficits: list[dict[str, Any]] = []
    min_score = int(report.get("min_score", 0))
    for item in report.get("guideline_scores", []):
        score = int(item.get("score", 0))
        if score >= min_score:
            continue
        guideline_id = str(item.get("guideline_id") or "")
        findings = ", ".join(str(entry) for entry in item.get("findings", []))
        deficits.append(
            {
                "deficit_id": f"quality:{guideline_id}",
                "type": "quality_gap",
                "severity": "medium",
                "guideline_id": guideline_id,
                "target_id": "",
                "obligation_unit_id": "",
                "distance_to_pass": max(0, min_score - score),
                "evidence_ref": "check_guideline_quality",
                "details": findings or f"quality score {score} < {min_score}",
            }
        )
    return deficits


def collect_example_deficits(report: dict[str, Any]) -> list[dict[str, Any]]:
    deficits: list[dict[str, Any]] = []
    for message in report.get("errors", []):
        text = str(message)
        guideline_match = re.match(r"(?P<guideline>RG-[A-Z0-9]+)", text)
        guideline_id = guideline_match.group("guideline") if guideline_match else ""
        deficits.append(
            {
                "deficit_id": f"example:{len(deficits) + 1}",
                "type": "example_gap",
                "severity": "high",
                "guideline_id": guideline_id,
                "target_id": "",
                "obligation_unit_id": "",
                "distance_to_pass": 1,
                "evidence_ref": "check_guideline_examples",
                "details": text,
            }
        )
    return deficits


def collect_known_good_alignment_deficits(report: dict[str, Any]) -> list[dict[str, Any]]:
    deficits: list[dict[str, Any]] = []

    for item in report.get("guideline_results", []):
        guideline_id = str(item.get("guideline_id") or "").strip()
        alignment_score = float(item.get("alignment_score") or 0.0)
        nearest = item.get("nearest_neighbors") or []
        nearest_id = ""
        if nearest:
            nearest_id = str((nearest[0] or {}).get("guideline_id") or "").strip()

        for flag in item.get("flags", []):
            flag_text = str(flag).strip()
            if not flag_text:
                continue

            severity = "medium"
            if flag_text in {"known_good_alignment_gap", "benchmark_similarity_gap"}:
                severity = "high"

            details = f"flag={flag_text} alignment_score={alignment_score:.3f}"
            if nearest_id:
                details = f"{details} nearest={nearest_id}"

            deficits.append(
                {
                    "deficit_id": f"known-good:{guideline_id}:{flag_text}",
                    "type": "known_good_alignment_gap",
                    "severity": severity,
                    "guideline_id": guideline_id,
                    "target_id": "",
                    "obligation_unit_id": "",
                    "distance_to_pass": round(max(0.0, 1.0 - alignment_score), 6),
                    "evidence_ref": "check_known_good_alignment",
                    "details": details,
                }
            )

    for message in report.get("errors", []):
        deficits.append(
            {
                "deficit_id": f"known-good:error:{len(deficits) + 1}",
                "type": "known_good_alignment_gap",
                "severity": "high",
                "guideline_id": "",
                "target_id": "",
                "obligation_unit_id": "",
                "distance_to_pass": 1,
                "evidence_ref": "check_known_good_alignment",
                "details": str(message),
            }
        )

    return deficits


def sort_deficits(deficits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(item: dict[str, Any]) -> tuple[Any, ...]:
        severity = str(item.get("severity") or "low")
        return (
            SEVERITY_ORDER.get(severity, 99),
            str(item.get("type") or ""),
            -float(item.get("distance_to_pass") or 0),
            str(item.get("deficit_id") or ""),
        )

    return sorted(deficits, key=key)


def observe_repo(
    root: Path,
    output_dir: Path,
    known_good_alignment_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reports = run_gates(root, output_dir, known_good_alignment_overrides)

    traceability_report = reports.get("check_traceability", {}).get("report", {})
    decomposition_report = reports.get("check_rule_decomposition", {}).get("report", {})
    fls_report = reports.get("check_fls_proxy_coverage", {}).get("report", {})
    quality_report = reports.get("check_guideline_quality", {}).get("report", {})
    example_report = reports.get("check_guideline_examples", {}).get("report", {})
    known_good_alignment_report = reports.get("check_known_good_alignment", {}).get("report", {})

    in_scope_obligations, covered_obligations = load_iso_obligation_sets(root)
    missing_obligations = sorted(in_scope_obligations - covered_obligations)
    coverage_ratio = 1.0
    if in_scope_obligations:
        coverage_ratio = len(covered_obligations & in_scope_obligations) / len(in_scope_obligations)

    deficits: list[dict[str, Any]] = []
    for obligation in missing_obligations:
        deficits.append(
            {
                "deficit_id": f"iso-obligation:{obligation}",
                "type": "iso_obligation_gap",
                "severity": "critical",
                "guideline_id": "",
                "target_id": "",
                "obligation_unit_id": obligation,
                "distance_to_pass": 1,
                "evidence_ref": "coverage_matrix",
                "details": "missing obligation coverage row",
            }
        )

    deficits.extend(collect_traceability_deficits(traceability_report))
    deficits.extend(collect_decomposition_deficits(decomposition_report))
    deficits.extend(collect_fls_deficits(fls_report))
    deficits.extend(collect_quality_deficits(quality_report))
    deficits.extend(collect_example_deficits(example_report))
    deficits.extend(collect_known_good_alignment_deficits(known_good_alignment_report))
    deficits.extend(collect_placeholder_gaps(root))

    sorted_deficits = sort_deficits(deficits)

    guideline_count = int(quality_report.get("guideline_count", 0))
    quality_gap_count = len([item for item in sorted_deficits if item["type"] == "quality_gap"])
    quality_pass_ratio = 1.0
    if guideline_count > 0:
        quality_pass_ratio = max(0.0, (guideline_count - quality_gap_count) / guideline_count)

    target_count = int(decomposition_report.get("target_count", 0))
    target_fanout_gap_count = len(
        [item for item in sorted_deficits if item["type"] == "target_fanout_gap"]
    )
    fls_target_count = int(fls_report.get("target_count", 0))
    fls_span_gap_count = len([item for item in sorted_deficits if item["type"] == "fls_span_gap"])
    fls_chapter_gap_count = len(
        [item for item in sorted_deficits if item["type"] == "fls_chapter_gap"]
    )
    placeholder_gap_count = len(
        [item for item in sorted_deficits if item["type"] == "placeholder_gap"]
    )
    example_gap_count = len([item for item in sorted_deficits if item["type"] == "example_gap"])
    known_good_alignment_gap_count = len(
        [item for item in sorted_deficits if item["type"] == "known_good_alignment_gap"]
    )
    traceability_gap_count = len(
        [item for item in sorted_deficits if item["type"] == "traceability_gap"]
    )

    metrics = {
        "runtime_failures": int(reports["_summary"]["runtime_failures"]),
        "policy_failures": int(reports["_summary"]["policy_failures"]),
        "iso_obligation_coverage": round(coverage_ratio, 6),
        "iso_obligation_gap_count": len(missing_obligations),
        "traceability_gap_count": traceability_gap_count,
        "decomposition_target_count": target_count,
        "target_fanout_gap_count": target_fanout_gap_count,
        "fls_target_count": fls_target_count,
        "fls_span_gap_count": fls_span_gap_count,
        "fls_chapter_gap_count": fls_chapter_gap_count,
        "fls_chapter_coverage": float(
            (fls_report.get("chapter_coverage") or {}).get("ratio") or 0.0
        ),
        "quality_gap_count": quality_gap_count,
        "quality_pass_ratio": round(quality_pass_ratio, 6),
        "quality_average_score": float(quality_report.get("average_score") or 0.0),
        "placeholder_gap_count": placeholder_gap_count,
        "example_gap_count": example_gap_count,
        "known_good_alignment_gap_count": known_good_alignment_gap_count,
        "known_good_alignment_average": float(
            known_good_alignment_report.get("average_alignment_score") or 0.0
        ),
        "total_deficit_count": len(sorted_deficits),
    }

    lanes = {
        "iso_lane_pass": (
            metrics["iso_obligation_gap_count"] == 0 and metrics["traceability_gap_count"] == 0
        ),
        "decomposition_lane_pass": metrics["target_fanout_gap_count"] == 0,
        "fls_lane_pass": (
            metrics["fls_span_gap_count"] == 0 and metrics["fls_chapter_gap_count"] == 0
        ),
        "quality_lane_pass": (
            metrics["quality_gap_count"] == 0
            and metrics["placeholder_gap_count"] == 0
            and metrics["example_gap_count"] == 0
            and metrics["known_good_alignment_gap_count"] == 0
        ),
        "hard_gate_pass": metrics["runtime_failures"] == 0 and metrics["policy_failures"] == 0,
    }

    observation = {
        **metrics,
        **lanes,
        "deficits": sorted_deficits,
        "reports": reports,
    }

    write_json(output_dir / "observation.json", observation)
    return observation
