from __future__ import annotations

import json
import shutil
import subprocess
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from retrieval.services import writer_publish_service
from retrieval.writer_host import publish


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _build_run(run_dir: Path, target_ids: list[str]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "writer_quality_gate_report.json", {"status": "pass"})
    (run_dir / "writer_review_packet.zip").write_bytes(b"placeholder zip")
    _write_json(
        run_dir / "writer_review_packet.manifest.json",
        {"artifact_count": 1, "artifacts": ["placeholder"]},
    )

    drafts: list[dict[str, Any]] = []
    amplification: list[dict[str, Any]] = []
    rationale: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    metadata: list[dict[str, Any]] = []
    for idx, target_id in enumerate(target_ids, start=1):
        drafts.append(
            {
                "target_id": target_id,
                "claim_to_evidence_map": [
                    {
                        "claim_id": f"{target_id}::claim::1",
                        "claim_text": f"{target_id} claim text",
                        "evidence_ids": [f"rust_reference::stmt-{idx}"],
                    }
                ],
            }
        )
        amplification.append(
            {
                "target_id": target_id,
                "output": {"guideline_amplification_text": f"Amplification for {target_id}."},
            }
        )
        rationale.append(
            {
                "target_id": target_id,
                "output": {"rationale_text": f"Rationale for {target_id}."},
            }
        )
        examples.append(
            {
                "target_id": target_id,
                "output": {
                    "non_compliant_narrative": f"Non-compliant narrative for {target_id}.",
                    "non_compliant_code": "fn bad() { unsafe { core::ptr::read_volatile(0 as *const u8); } }",
                    "compliant_narrative": f"Compliant narrative for {target_id}.",
                    "compliant_code": "fn good() { let value = Some(1u8); assert_eq!(value, Some(1)); }",
                    "non_compliant_miri_intent": "skip",
                    "non_compliant_miri_skip_justification": "Synthetic validation fixture.",
                    "compliant_miri_intent": "check",
                    "compliant_miri_skip_justification": "",
                },
            }
        )
        metadata.append(
            {
                "target_id": target_id,
                "output": {
                    "tags": ["unsafe", "error-handling"],
                    "bibliography_rows": [
                        {
                            "citation_key": f"cite_{idx}",
                            "author": "Reference",
                            "title": f"Reference {idx}",
                            "url": f"https://example.test/{target_id.lower()}",
                        }
                    ],
                    "fls_candidate": {
                        "statement": f"Generated guideline for {target_id}",
                        "category": "safety required",
                    },
                },
            }
        )

    _write_jsonl(run_dir / "drafts.jsonl", drafts)
    subagent_root = run_dir / "writer_subagent_outputs"
    _write_jsonl(subagent_root / "amplification_author.jsonl", amplification)
    _write_jsonl(subagent_root / "rationale_author.jsonl", rationale)
    _write_jsonl(subagent_root / "example_author.jsonl", examples)
    _write_jsonl(subagent_root / "metadata_citation_curator.jsonl", metadata)


def _fake_map_publish_record(
    row: dict[str, Any], *, resolution_report_root: Path | None = None
) -> dict[str, Any]:
    target_id = str(row["draft"]["target_id"])
    suffix = target_id.rsplit("-", 1)[-1]
    order = int(suffix) if suffix.isdigit() else 1
    chapter = "unsafety" if order % 2 else "exceptions-and-errors"
    report_path = ""
    if resolution_report_root is not None:
        resolution_report_root.mkdir(parents=True, exist_ok=True)
        path = resolution_report_root / f"{target_id.lower()}.json"
        _write_json(path, {"target_id": target_id, "decision": {"reason_code": "ACCEPTED"}})
        report_path = str(path)
    token = target_id.lower().replace("-", "_")
    return {
        "target_id": target_id,
        "guideline_id": f"gui_{token}",
        "filename": f"gui_{token}.rst",
        "chapter": chapter,
        "title": f"Generated guideline for {target_id}",
        "category": "required",
        "status": "draft",
        "release": "1.85.1",
        "fls_id": f"fls_demo_{order}",
        "fls_resolution": {"reason_code": "ACCEPTED", "accepted": True},
        "fls_resolution_report": report_path,
        "decidability": "undecidable",
        "scope": "module",
        "tags": ["unsafe"] if chapter == "unsafety" else ["defect"],
    }


def _fake_conformance(*, repo_root: Path, report_dir: Path) -> dict[str, Any]:
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"status": "pass", "repo_root": str(repo_root)}
    _write_json(report_dir / "writer_conformance_report.json", payload)
    return payload


def _make_finalize(commit_result: dict[str, Any]):
    def _finalize(**_: Any) -> dict[str, Any]:
        return commit_result

    return _finalize


def _fake_push(**kwargs: Any) -> dict[str, Any]:
    return {"pushed": True, "branch": str(kwargs["branch"])}


def run_validation(*, root: Path, output_root: Path) -> dict[str, Any]:
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    source_guidelines = (root / "../safety-critical-rust-coding-guidelines").resolve()
    working_guidelines = output_root / "guidelines_repo_clone"
    subprocess.run(
        ["git", "clone", "--quiet", str(source_guidelines), str(working_guidelines)], check=True
    )

    original_repo_loader = publish._load_guidelines_repo_root
    original_map_publish_record = publish.map_publish_record
    original_run_conformance = publish.run_conformance
    original_finalize_commit = publish.finalize_commit
    original_push_branch = publish.push_branch

    publish._load_guidelines_repo_root = lambda _: working_guidelines
    publish.map_publish_record = _fake_map_publish_record
    publish.run_conformance = _fake_conformance

    scenarios = [
        (
            "persist_1_target_run",
            ["PERSIST-TARGET-1"],
            {"committed": False, "commit": "", "message": "no changes"},
            {"expect_status": "no_changes", "expect_cleanup": False},
        ),
        (
            "persist_3_target_run",
            ["PERSIST-TARGET-1", "PERSIST-TARGET-2", "PERSIST-TARGET-3"],
            {"committed": True, "commit": "demo123", "message": "commit"},
            {"expect_status": "pass", "expect_cleanup": True},
        ),
    ]
    results: list[dict[str, Any]] = []
    try:
        for run_name, targets, commit_result, expect in scenarios:
            run_dir = output_root / run_name
            _build_run(run_dir, targets)
            publish.finalize_commit = _make_finalize(commit_result)
            publish.push_branch = _fake_push

            code = writer_publish_service.run(
                Namespace(
                    run_dir=str(run_dir),
                    mode="publishable",
                    dry_run=False,
                    keep_worktree=False,
                    output="",
                ),
                root=root,
            )
            publish_root = (
                root / ".cache" / "sqlite_kb" / "reports" / "writer_publish" / run_dir.name
            )
            report_path = publish_root / "writer_publish_report.json"
            packet_path = publish_root / "writer_publish_review_packet.zip"
            manifest_path = publish_root / "writer_publish_review_packet.manifest.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            snapshot_path = Path(str(report["export_snapshot"]["path"]))
            worktree_path = Path(str(report["worktree"])) if report.get("worktree") else None
            exported_files = sorted(
                str(path.relative_to(snapshot_path)) for path in snapshot_path.rglob("*.rst")
            )

            if code != 0:
                raise RuntimeError(f"{run_name} exited with code {code}")
            if str(report.get("status", "")) != str(expect["expect_status"]):
                raise RuntimeError(f"{run_name} status mismatch: {report}")
            if (
                not report_path.exists()
                or not snapshot_path.exists()
                or not packet_path.exists()
                or not manifest_path.exists()
            ):
                raise RuntimeError(f"{run_name} missing durable artifacts")
            if not exported_files:
                raise RuntimeError(f"{run_name} exported no rst files")
            if bool(report["cleanup"]["performed"]) is not bool(expect["expect_cleanup"]):
                raise RuntimeError(f"{run_name} cleanup mismatch: {report['cleanup']}")
            if worktree_path is not None and bool(worktree_path.exists()) is bool(
                expect["expect_cleanup"]
            ):
                raise RuntimeError(f"{run_name} worktree persistence mismatch: {worktree_path}")

            results.append(
                {
                    "run_name": run_name,
                    "status": report["status"],
                    "cleanup": report["cleanup"],
                    "report_path": str(report_path),
                    "snapshot_path": str(snapshot_path),
                    "packet_path": str(packet_path),
                    "worktree": str(worktree_path) if worktree_path is not None else "",
                    "worktree_exists_after_run": bool(worktree_path.exists())
                    if worktree_path
                    else False,
                    "exported_rst_files": exported_files,
                }
            )
    finally:
        publish._load_guidelines_repo_root = original_repo_loader
        publish.map_publish_record = original_map_publish_record
        publish.run_conformance = original_run_conformance
        publish.finalize_commit = original_finalize_commit
        publish.push_branch = original_push_branch

    summary = {
        "validation_root": str(output_root),
        "results": results,
    }
    summary_path = output_root / "publish_persistence_validation_summary.json"
    _write_json(summary_path, summary)
    summary["summary_path"] = str(summary_path)
    return summary


def parse_args() -> Namespace:
    parser = ArgumentParser(
        description="Validate 1-target and 3-target publish artifact persistence"
    )
    parser.add_argument(
        "--output-root",
        default=str(ROOT / ".cache" / "sqlite_kb" / "reports" / "publish_persistence_validation"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = run_validation(root=ROOT, output_root=Path(str(args.output_root)).resolve())
    print(json.dumps(summary, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
