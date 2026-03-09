from __future__ import annotations

import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retrieval.corpora.config_loader import load_corpus_runtime_defaults
from retrieval.services.utils import _write_json


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_artifact_into_packet(*, source: Path, report_dir: Path, label: str) -> Path:
    safe_label = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in label)
    target = report_dir / "artifacts" / f"{safe_label}{source.suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def _collect_testsuites(root: ET.Element) -> list[ET.Element]:
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if root.tag == "testsuites" and not suites:
        suites = [root]
    return suites


def _parse_junit_summary(path: Path, *, artifact: dict[str, Any]) -> dict[str, Any]:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    suites = _collect_testsuites(root)
    tests = 0
    failures = 0
    errors = 0
    skipped = 0
    testcase_identities: set[str] = set()
    suite_names: set[str] = set()
    timestamps: list[str] = []
    for suite in suites:
        suite_name = str(suite.attrib.get("name", "")).strip()
        if suite_name:
            suite_names.add(suite_name)
        timestamp = str(suite.attrib.get("timestamp", "")).strip()
        if timestamp:
            timestamps.append(timestamp)
        tests += int(suite.attrib.get("tests", 0) or 0)
        failures += int(suite.attrib.get("failures", 0) or 0)
        errors += int(suite.attrib.get("errors", 0) or 0)
        skipped += int(suite.attrib.get("skipped", 0) or 0)
        for testcase in suite.findall("testcase"):
            classname = str(testcase.attrib.get("classname", "")).strip()
            name = str(testcase.attrib.get("name", "")).strip()
            if classname or name:
                testcase_identities.add(f"{classname}::{name}")
    min_tests = int(artifact.get("min_tests", 1) or 1)
    if tests <= 0:
        raise RuntimeError(f"WS7 prework JUnit artifact has no executed tests: {path}")
    if tests < min_tests:
        raise RuntimeError(
            f"WS7 prework JUnit artifact has too few executed tests: {path} tests={tests} min_tests={min_tests}"
        )
    if failures or errors:
        raise RuntimeError(
            f"WS7 prework JUnit artifact is not passing: {path} failures={failures} errors={errors}"
        )
    expected_testcase_substrings = [
        str(value).strip()
        for value in list(artifact.get("expected_testcase_substrings") or [])
        if str(value).strip()
    ]
    for expected in expected_testcase_substrings:
        if not any(expected in identity for identity in testcase_identities):
            raise RuntimeError(
                f"WS7 prework JUnit artifact missing expected testcase identity '{expected}': {path}"
            )
    return {
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "suite_names": sorted(suite_names),
        "timestamp": max(timestamps) if timestamps else "",
        "testcase_count": len(testcase_identities),
    }


def _parse_chunk_first_report(path: Path, *, expected_corpus: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"WS7 prework chunk-first report must be a JSON object: {path}")
    if str(payload.get("corpus", "")).strip() != expected_corpus:
        raise RuntimeError(
            f"WS7 prework chunk-first report corpus mismatch: expected {expected_corpus}, got {payload.get('corpus')}"
        )
    if not bool(payload.get("passed", False)):
        raise RuntimeError(f"WS7 prework chunk-first report is not passing: {path}")
    db_path = str(payload.get("db_path", "")).strip()
    db_sha256 = str(payload.get("db_sha256", "")).strip()
    if not db_path or not db_sha256:
        raise RuntimeError(f"WS7 prework chunk-first report missing DB identity fields: {path}")
    db_file = Path(db_path)
    if not db_file.exists() or not db_file.is_file():
        raise RuntimeError(f"WS7 prework chunk-first report points to missing DB: {path}")
    actual_db_sha256 = _sha256(db_file)
    if actual_db_sha256 != db_sha256:
        raise RuntimeError(
            "WS7 prework chunk-first report DB hash mismatch: "
            f"expected {db_sha256}, actual {actual_db_sha256}: {path}"
        )
    latest_migration_id = str(payload.get("latest_migration_id", "") or "").strip()
    if not latest_migration_id:
        raise RuntimeError(f"WS7 prework chunk-first report missing latest_migration_id: {path}")
    schema_user_version = int(payload.get("schema_user_version", 0) or 0)
    if schema_user_version <= 0:
        raise RuntimeError(
            f"WS7 prework chunk-first report has invalid schema_user_version: {path}"
        )
    raw_mapping = payload.get("chunk_fts_mapping")
    mapping = raw_mapping if isinstance(raw_mapping, dict) else {}
    if not bool(mapping.get("passed", False)):
        raise RuntimeError(f"WS7 prework chunk-first mapping is not passing: {path}")
    if int(payload.get("chunk_count", 0) or 0) <= 0:
        raise RuntimeError(f"WS7 prework chunk-first report has no chunks: {path}")
    if int(payload.get("chunks_fts_count", 0) or 0) <= 0:
        raise RuntimeError(f"WS7 prework chunk-first report has no chunks_fts rows: {path}")
    chunk_fts_rowids_count = int(mapping.get("chunk_fts_rowids_count", 0) or 0)
    if chunk_fts_rowids_count <= 0:
        raise RuntimeError(f"WS7 prework chunk-first report has no chunk_fts_rowids rows: {path}")
    if chunk_fts_rowids_count != int(payload.get("chunk_count", 0) or 0):
        raise RuntimeError(
            f"WS7 prework chunk-first report mapping count does not match chunks: {path}"
        )
    return {
        "corpus": expected_corpus,
        "db_path": db_path,
        "db_sha256": db_sha256,
        "schema_user_version": schema_user_version,
        "latest_migration_id": latest_migration_id,
        "latest_snapshot_id": str(payload.get("latest_snapshot_id", "") or ""),
        "chunk_count": int(payload.get("chunk_count", 0) or 0),
        "chunks_fts_count": int(payload.get("chunks_fts_count", 0) or 0),
        "checked_at": str(payload.get("checked_at", "") or ""),
    }


def _validate_artifact(path: Path, artifact: dict[str, Any]) -> dict[str, Any]:
    artifact_type = str(artifact.get("type", "file")).strip() or "file"
    if artifact_type == "junit_xml":
        return _parse_junit_summary(path, artifact=artifact)
    if artifact_type == "chunk_first_report":
        expected_corpus = str(artifact.get("expected_corpus", "")).strip()
        if not expected_corpus:
            raise RuntimeError(f"chunk_first_report artifact missing expected_corpus: {path}")
        return _parse_chunk_first_report(path, expected_corpus=expected_corpus)
    return {}


def write_ws7_prework_closure_packet(
    *,
    report_dir: Path,
    priorities: list[dict[str, Any]],
    proof_artifacts: list[dict[str, Any]],
    corpora: list[str],
    deferred_items: list[str] | None = None,
    allow_incomplete: bool = False,
) -> tuple[Path, Path]:
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    artifact_records: list[dict[str, Any]] = []
    artifact_labels: set[str] = set()
    missing: list[str] = []
    invalid: list[str] = []
    db_identities: list[dict[str, Any]] = []
    for artifact in proof_artifacts:
        source_path = Path(str(artifact.get("path", "")).strip())
        label = str(artifact.get("label", "")).strip() or str(source_path)
        if not source_path.exists() or not source_path.is_file():
            missing.append(label)
            continue
        try:
            validated_summary = _validate_artifact(source_path, artifact)
        except Exception as exc:
            invalid.append(f"{label}: {exc}")
            continue
        frozen_path = _copy_artifact_into_packet(
            source=source_path,
            report_dir=report_dir,
            label=label,
        )
        artifact_record = {
            "label": label,
            "path": str(frozen_path),
            "source_path": str(source_path),
            "priority": int(artifact.get("priority", 0) or 0),
            "sha256": _sha256(frozen_path),
            "size_bytes": int(frozen_path.stat().st_size),
            "type": str(artifact.get("type", "file") or "file"),
        }
        if validated_summary:
            artifact_record["validated_summary"] = validated_summary
        artifact_records.append(artifact_record)
        artifact_labels.add(label)
        if artifact_record["type"] == "chunk_first_report" and validated_summary:
            db_identities.append(validated_summary)
    if (missing or invalid) and not allow_incomplete:
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if invalid:
            details.append(f"invalid={invalid}")
        raise RuntimeError("WS7 prework closure artifacts incomplete: " + "; ".join(details))

    computed_priorities: list[dict[str, Any]] = []
    for item in priorities:
        priority = int(item.get("priority", 0) or 0)
        required_labels = [
            str(label).strip()
            for label in list(item.get("required_artifact_labels") or [])
            if str(label).strip()
        ]
        if not required_labels:
            raise RuntimeError(
                f"WS7 prework closure priority {priority} missing required_artifact_labels"
            )
        missing_labels = [label for label in required_labels if label not in artifact_labels]
        computed_priorities.append(
            {
                "priority": priority,
                "required_artifact_labels": required_labels,
                "missing_artifact_labels": missing_labels,
                "status": "pass" if not missing_labels else "fail",
            }
        )

    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "ws7_prework_closure_report.json"
    manifest_path = report_dir / "ws7_prework_closure_manifest.json"

    report = {
        "generated_at": timestamp,
        "policy_version": 2,
        "prework_only": True,
        "non_authoritative_for_full_ws7_runtime": True,
        "corpora": sorted({str(corpus).strip() for corpus in corpora if str(corpus).strip()}),
        "priorities": computed_priorities,
        "proof_artifacts": artifact_records,
        "db_identities": sorted(db_identities, key=lambda item: (item["corpus"], item["db_path"])),
        "missing_artifacts": missing,
        "invalid_artifacts": invalid,
        "deferred_items": list(deferred_items or []),
        "closure_custody": {
            "artifact_freeze_mode": "packet_local_copies",
            "db_identity_verification": "rehashed_from_db_path_during_packet_generation",
        },
        "status": (
            "pass"
            if not missing
            and not invalid
            and computed_priorities
            and all(str(item.get("status", "")) == "pass" for item in computed_priorities)
            else "fail"
        ),
    }
    _write_json(report_path, report)

    manifest = {
        "generated_at": timestamp,
        "policy_version": 2,
        "prework_only": True,
        "non_authoritative_for_full_ws7_runtime": True,
        "corpora": report["corpora"],
        "db_identities": report["db_identities"],
        "included_files": [
            {
                "path": str(report_path),
                "sha256": _sha256(report_path),
            },
            {
                "path": str(manifest_path),
                "sha256": "",
            },
            *[
                {
                    "path": record["path"],
                    "sha256": record["sha256"],
                }
                for record in artifact_records
            ],
        ],
    }
    _write_json(manifest_path, manifest)
    manifest["included_files"][1]["sha256"] = _sha256(manifest_path)
    _write_json(manifest_path, manifest)
    return report_path, manifest_path


def _current_chunk_first_artifact_specs(*, root: Path) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for corpus in ("fls_spec", "core_docs", "rust_reference"):
        defaults = load_corpus_runtime_defaults(root=root, corpus=corpus)
        specs.append(
            {
                "label": f"{corpus}_current_chunk_first",
                "path": defaults.report_root / "current_chunk_first_validation.json",
                "priority": 5,
                "type": "chunk_first_report",
                "expected_corpus": corpus,
            }
        )
    return specs


def generate_ws7_prework_closure_packet(
    *,
    report_dir: Path,
    deferred_items: list[str] | None = None,
    root: Path | None = None,
    allow_incomplete: bool = False,
) -> tuple[Path, Path]:
    project_root = root or Path(__file__).resolve().parents[3]
    artifact_specs = [
        {
            "label": "priority1_3",
            "path": report_dir / "priority1_3.xml",
            "priority": 1,
            "type": "junit_xml",
            "min_tests": 10,
            "expected_testcase_substrings": [
                "test_chunk_first_runbook_prereqs",
                "test_provenance_guard",
            ],
        },
        {
            "label": "priority2_5",
            "path": report_dir / "priority2_5.xml",
            "priority": 2,
            "type": "junit_xml",
            "min_tests": 10,
            "expected_testcase_substrings": [
                "test_query_rust_reference",
                "test_fls_step6",
            ],
        },
        {
            "label": "priority4_supporting",
            "path": report_dir / "priority4_supporting.xml",
            "priority": 4,
            "type": "junit_xml",
            "min_tests": 8,
            "expected_testcase_substrings": [
                "test_query_core_docs",
                "test_query_set_verifier",
            ],
        },
        {
            "label": "supporting",
            "path": report_dir / "supporting.xml",
            "priority": 5,
            "type": "junit_xml",
            "min_tests": 4,
            "expected_testcase_substrings": [
                "test_fls_candidate_search",
                "test_ws7_prework_closure",
            ],
        },
        *_current_chunk_first_artifact_specs(root=project_root),
    ]
    priorities = [
        {"priority": 1, "required_artifact_labels": ["priority1_3"]},
        {"priority": 2, "required_artifact_labels": ["priority2_5"]},
        {"priority": 3, "required_artifact_labels": ["priority1_3"]},
        {"priority": 4, "required_artifact_labels": ["priority4_supporting"]},
        {
            "priority": 5,
            "required_artifact_labels": [
                "priority2_5",
                "priority4_supporting",
                "supporting",
                "fls_spec_current_chunk_first",
                "core_docs_current_chunk_first",
                "rust_reference_current_chunk_first",
            ],
        },
    ]
    return write_ws7_prework_closure_packet(
        report_dir=report_dir,
        priorities=priorities,
        proof_artifacts=artifact_specs,
        corpora=["fls_spec", "core_docs", "rust_reference"],
        deferred_items=deferred_items,
        allow_incomplete=allow_incomplete,
    )


def maybe_refresh_ws7_prework_closure_packet(
    *,
    root: Path,
    report_dir: Path | None = None,
    deferred_items: list[str] | None = None,
) -> tuple[Path, Path] | None:
    target_dir = report_dir or (root / ".cache" / "sqlite_kb" / "reports" / "ws7_prework_current")
    try:
        return generate_ws7_prework_closure_packet(
            report_dir=target_dir,
            deferred_items=deferred_items,
            root=root,
            allow_incomplete=True,
        )
    except Exception:
        return None
