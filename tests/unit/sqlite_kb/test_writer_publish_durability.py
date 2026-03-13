from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.services import writer_publish_service  # noqa: E402
from retrieval.writer_host import publish  # noqa: E402


def _seed_exported_guidelines(worktree_root: Path) -> None:
    out = worktree_root / "src" / "coding-guidelines" / "unsafety"
    out.mkdir(parents=True, exist_ok=True)
    (out / "gui_demo.rst").write_text("Guideline\n=========\n", encoding="utf-8")
    (out / "index.rst").write_text("Unsafety\n========\n", encoding="utf-8")


def _seed_review_mode_fls_checks(worktree_root: Path) -> None:
    fls_checks = worktree_root / "exts" / "coding_guidelines" / "fls_checks.py"
    fls_checks.parent.mkdir(parents=True, exist_ok=True)
    fls_checks.write_text(
        "import json\n"
        "import re\n"
        "\n"
        "from sphinx.errors import SphinxError\n"
        "from .common import logger\n"
        "\n"
        "\n"
        "class FLSValidationError(SphinxError):\n"
        "    category = 'FLS Validation Error'\n"
        "\n"
        "\n"
        "def check_fls_ids_correct(app, env, fls_ids):\n"
        "    for need_id in ['gui_demo']:\n"
        "        fls_value = 'fls_UNRESOLVED'\n"
        "            # Check if the FLS ID exists in the gathered IDs\n"
        "            if fls_value not in fls_ids:\n"
        "                raise FLSValidationError(need_id)\n",
        encoding="utf-8",
    )


def _export_payload(worktree_root: Path) -> dict[str, object]:
    return {
        "status": "pass",
        "output_root": str(worktree_root / "src" / "coding-guidelines"),
        "export": {
            "file_count": 2,
            "generated_files": [
                str(worktree_root / "src" / "coding-guidelines" / "unsafety" / "gui_demo.rst"),
                str(worktree_root / "src" / "coding-guidelines" / "unsafety" / "index.rst"),
            ],
        },
    }


def _audited_rows(tmp_path: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    audit_path = tmp_path / "publishability_audit.json"
    audit_path.write_text("{}\n", encoding="utf-8")
    return (
        {
            "status": "pass",
            "blocked_count": 0,
            "path": str(audit_path),
        },
        [
            {
                "publishable": True,
                "row": {
                    "amplification": {"guideline_amplification_text": "body"},
                    "rationale": {"rationale_text": "why"},
                    "examples": {
                        "non_compliant_narrative": "bad",
                        "non_compliant_code": "unsafe { bad(); }",
                        "compliant_narrative": "good",
                        "compliant_code": "good();",
                    },
                    "metadata": {"bibliography_rows": []},
                },
                "mapping": {
                    "target_id": "RET-ISSUE-001",
                    "guideline_id": "gui_demo",
                    "filename": "gui_demo.rst",
                    "chapter": "unsafety",
                    "title": "Review title",
                    "category": "advisory",
                    "status": "draft",
                    "release": "1.85.1",
                    "fls_id": "fls_demo",
                    "fls_resolution": {"reason_code": "ACCEPTED"},
                    "fls_resolution_report": str(tmp_path / "fls.json"),
                    "publishability": {"publishable": True, "reason_code": "ACCEPTED"},
                    "decidability": "undecidable",
                    "scope": "module",
                    "tags": ["unsafe"],
                },
            }
        ],
    )


def test_run_publish_from_run_preserves_snapshot_on_conformance_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "guidelines_repo"
    repo_root.mkdir()
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    removed: list[tuple[Path, Path]] = []

    monkeypatch.setattr(publish, "_load_guidelines_repo_root", lambda root: repo_root)
    monkeypatch.setattr(
        publish,
        "create_worktree",
        lambda repo_root, cache_root: {
            "branch": "writer-publish-demo",
            "worktree": str(worktree_root),
        },
    )
    monkeypatch.setattr(
        publish,
        "_build_publishability_audit",
        lambda **kwargs: _audited_rows(tmp_path),
    )

    def fake_export(**kwargs):
        _seed_exported_guidelines(worktree_root)
        return _export_payload(worktree_root)

    monkeypatch.setattr(publish, "run_export_rst", fake_export)
    monkeypatch.setattr(
        publish,
        "status_porcelain",
        lambda **kwargs: [
            {"code": "??", "path": "src/coding-guidelines/unsafety/gui_demo.rst"},
            {"code": " M", "path": "src/coding-guidelines/unsafety/index.rst"},
        ],
    )
    monkeypatch.setattr(publish, "run_conformance", lambda **kwargs: {"status": "fail"})
    monkeypatch.setattr(
        publish,
        "remove_worktree",
        lambda repo_root, worktree_root: removed.append((repo_root, worktree_root)),
    )

    report = publish.run_publish_from_run(
        root=tmp_path,
        run_dir=run_dir,
        mode="publishable",
        dry_run=False,
    )

    snapshot_path = Path(str(report["export_snapshot"]["path"]))
    assert report["status"] == "fail"
    assert report["failure_code"] == "CONFORMANCE_FAILED"
    assert snapshot_path.exists()
    assert (snapshot_path / "unsafety" / "gui_demo.rst").exists()
    assert (snapshot_path / "THIS_RUN_CHANGES.md").exists()
    assert Path(str(report["export_delta"]["internal_rendered_candidate_manifest_path"])).exists()
    assert report["export_delta"]["created_files"] == ["unsafety/gui_demo.rst"]
    assert report["export_delta"]["modified_files"] == ["unsafety/index.rst"]
    assert report["cleanup"]["performed"] is False
    assert report["cleanup"]["reason"] == "preserved_after_non_pass"
    assert removed == []


def test_run_publish_from_run_reports_no_changes_and_keeps_artifacts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "guidelines_repo"
    repo_root.mkdir()
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    monkeypatch.setattr(publish, "_load_guidelines_repo_root", lambda root: repo_root)
    monkeypatch.setattr(
        publish,
        "create_worktree",
        lambda repo_root, cache_root: {
            "branch": "writer-publish-demo",
            "worktree": str(worktree_root),
        },
    )
    monkeypatch.setattr(
        publish,
        "_build_publishability_audit",
        lambda **kwargs: _audited_rows(tmp_path),
    )
    monkeypatch.setattr(
        publish,
        "run_export_rst",
        lambda **kwargs: (
            _seed_exported_guidelines(worktree_root) or _export_payload(worktree_root)
        ),
    )
    monkeypatch.setattr(publish, "status_porcelain", lambda **kwargs: [])
    monkeypatch.setattr(publish, "run_conformance", lambda **kwargs: {"status": "pass"})
    monkeypatch.setattr(
        publish,
        "finalize_commit",
        lambda **kwargs: {"committed": False, "commit": "", "message": "msg"},
    )
    monkeypatch.setattr(
        publish,
        "push_branch",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("push should not run")),
    )

    report = publish.run_publish_from_run(
        root=tmp_path,
        run_dir=run_dir,
        mode="publishable",
        dry_run=False,
    )

    assert report["status"] == "no_changes"
    assert report["failure_code"] == "NO_CHANGES"
    assert Path(str(report["export_snapshot"]["path"])).exists()
    assert Path(str(report["export_delta"]["manifest_path"])).exists()
    assert Path(str(report["export_delta"]["note_path"])).exists()
    assert Path(str(report["export_delta"]["internal_rendered_candidate_manifest_path"])).exists()
    assert report["export_delta"]["unchanged_generated_files"] == [
        "unsafety/gui_demo.rst",
        "unsafety/index.rst",
    ]
    assert report["cleanup"]["performed"] is False


def test_run_publish_from_run_cleans_worktree_after_success(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "guidelines_repo"
    repo_root.mkdir()
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    removed: list[tuple[Path, Path]] = []

    monkeypatch.setattr(publish, "_load_guidelines_repo_root", lambda root: repo_root)
    monkeypatch.setattr(
        publish,
        "create_worktree",
        lambda repo_root, cache_root: {
            "branch": "writer-publish-demo",
            "worktree": str(worktree_root),
        },
    )
    monkeypatch.setattr(
        publish,
        "_build_publishability_audit",
        lambda **kwargs: _audited_rows(tmp_path),
    )
    monkeypatch.setattr(
        publish,
        "run_export_rst",
        lambda **kwargs: (
            _seed_exported_guidelines(worktree_root) or _export_payload(worktree_root)
        ),
    )
    monkeypatch.setattr(
        publish,
        "status_porcelain",
        lambda **kwargs: [
            {"code": "??", "path": "src/coding-guidelines/unsafety/gui_demo.rst"},
            {"code": " M", "path": "src/coding-guidelines/unsafety/index.rst"},
        ],
    )
    monkeypatch.setattr(publish, "run_conformance", lambda **kwargs: {"status": "pass"})
    monkeypatch.setattr(
        publish,
        "finalize_commit",
        lambda **kwargs: {"committed": True, "commit": "abc123", "message": "msg"},
    )
    monkeypatch.setattr(
        publish, "push_branch", lambda **kwargs: {"pushed": True, "branch": "writer-publish-demo"}
    )
    monkeypatch.setattr(
        publish,
        "remove_worktree",
        lambda repo_root, worktree_root: removed.append((repo_root, worktree_root)),
    )

    report = publish.run_publish_from_run(
        root=tmp_path,
        run_dir=run_dir,
        mode="publishable",
        dry_run=False,
    )

    assert report["status"] == "pass"
    assert report["push"]["pushed"] is True
    assert report["cleanup"]["performed"] is True
    assert report["cleanup"]["reason"] == "success_cleanup"
    assert Path(str(report["export_delta"]["manifest_path"])).exists()
    assert Path(str(report["export_delta"]["note_path"])).exists()
    assert (run_dir / "writer_conformance_report.json").exists()
    assert removed == [(repo_root, worktree_root)]


def test_run_publish_from_run_review_mode_exports_without_push(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "guidelines_repo"
    repo_root.mkdir()
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    removed: list[tuple[Path, Path]] = []

    monkeypatch.setattr(publish, "_load_guidelines_repo_root", lambda root: repo_root)
    monkeypatch.setattr(
        publish,
        "_build_publishability_audit",
        lambda **kwargs: (
            {
                "status": "blocked",
                "blocked_count": 1,
                "path": str(tmp_path / "publishability_audit.json"),
            },
            [
                {
                    "publishable": False,
                    "row": {
                        "amplification": {"guideline_amplification_text": "body"},
                        "rationale": {"rationale_text": "why"},
                        "examples": {
                            "non_compliant_narrative": "bad",
                            "non_compliant_code": "unsafe { bad(); }",
                            "compliant_narrative": "good",
                            "compliant_code": "good();",
                        },
                        "metadata": {"bibliography_rows": []},
                    },
                    "mapping": {
                        "target_id": "RET-ISSUE-001",
                        "guideline_id": "gui_demo",
                        "filename": "gui_demo.rst",
                        "chapter": "unsafety",
                        "title": "Review title",
                        "category": "advisory",
                        "status": "draft",
                        "release": "1.85.1",
                        "fls_id": "fls_UNRESOLVED",
                        "fls_resolution": {"reason_code": "CHAPTER_MISMATCH"},
                        "fls_resolution_report": str(tmp_path / "fls.json"),
                        "publishability": {"publishable": False, "reason_code": "CHAPTER_MISMATCH"},
                        "decidability": "undecidable",
                        "scope": "module",
                        "tags": ["unsafe"],
                    },
                }
            ],
        ),
    )
    monkeypatch.setattr(
        publish,
        "create_worktree",
        lambda repo_root, cache_root: (
            _seed_review_mode_fls_checks(worktree_root)
            or {
                "branch": "writer-publish-demo",
                "worktree": str(worktree_root),
            }
        ),
    )
    monkeypatch.setattr(
        publish,
        "run_export_rst",
        lambda **kwargs: (
            _seed_exported_guidelines(worktree_root) or _export_payload(worktree_root)
        ),
    )
    monkeypatch.setattr(
        publish,
        "status_porcelain",
        lambda **kwargs: [
            {"code": "??", "path": "src/coding-guidelines/unsafety/gui_demo.rst"},
            {"code": " M", "path": "src/coding-guidelines/unsafety/index.rst"},
        ],
    )
    monkeypatch.setattr(publish, "run_conformance", lambda **kwargs: {"status": "pass"})
    monkeypatch.setattr(
        publish,
        "finalize_commit",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("commit should not run in review mode")
        ),
    )
    monkeypatch.setattr(
        publish,
        "push_branch",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("push should not run in review mode")
        ),
    )
    monkeypatch.setattr(
        publish,
        "remove_worktree",
        lambda repo_root, worktree_root: removed.append((repo_root, worktree_root)),
    )

    report = publish.run_publish_from_run(
        root=tmp_path,
        run_dir=run_dir,
        mode="review",
        dry_run=False,
    )

    assert report["status"] == "review_export_pass"
    assert report["publishability_audit"]["blocked_count"] == 1
    assert report["review_mode_worktree"]["status"] == "patched"
    assert report["commit"]["committed"] is False
    assert report["push"]["pushed"] is False
    assert Path(str(report["export_snapshot"]["path"])).exists()
    assert (run_dir / "writer_conformance_report.json").exists()
    assert removed == [(repo_root, worktree_root)]


def test_canonicalize_exported_bibliography_rewrites_generated_entries(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "coding-guidelines"
    existing = source_root / "expressions" / "gui_existing.rst"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text(
        ".. bibliography::\n"
        "   :id: bib_existing\n\n"
        "   .. list-table::\n"
        "      :header-rows: 0\n\n"
        "     * - :bibentry:`gui_existing:RUST-REF-UNION`\n"
        '       - The Rust Reference. "Unions." https://doc.rust-lang.org/reference/items/unions.html\n',
        encoding="utf-8",
    )
    generated = source_root / "expressions" / "gui_generated.rst"
    generated.write_text(
        "Refer to :cite:`gui_generated:RUSTREF-UNIONS-BORROWS`.\n\n"
        ".. bibliography::\n"
        "   :id: bib_generated\n\n"
        "   .. list-table::\n"
        "      :header-rows: 0\n\n"
        "     * - :bibentry:`gui_generated:RUSTREF-UNIONS-BORROWS`\n"
        '       - Rust Project Developers. "The Rust Reference - Unions." https://doc.rust-lang.org/reference/items/unions.html\n',
        encoding="utf-8",
    )

    result = publish._canonicalize_exported_bibliography(
        source_root=source_root,
        generated_files=[str(generated)],
    )

    text = generated.read_text(encoding="utf-8")
    assert result["status"] == "patched"
    assert result["updated_entry_count"] == 1
    assert ":cite:`gui_generated:RUST-REF-UNION`" in text
    assert ":bibentry:`gui_generated:RUST-REF-UNION`" in text
    assert (
        'The Rust Reference. "Unions." https://doc.rust-lang.org/reference/items/unions.html'
        in text
    )


def test_run_publish_from_run_audit_only_blocks_before_worktree(
    monkeypatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "guidelines_repo"
    repo_root.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    monkeypatch.setattr(publish, "_load_guidelines_repo_root", lambda root: repo_root)
    monkeypatch.setattr(
        publish,
        "_build_publishability_audit",
        lambda **kwargs: (
            {
                "status": "blocked",
                "blocked_count": 2,
                "path": str(tmp_path / "publishability_audit.json"),
            },
            [],
        ),
    )
    monkeypatch.setattr(
        publish,
        "create_worktree",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("worktree should not be created for audit-only")
        ),
    )

    report = publish.run_publish_from_run(
        root=tmp_path,
        run_dir=run_dir,
        mode="publishable",
        dry_run=False,
        audit_only=True,
    )

    assert report["status"] == "publishability_blocked"
    assert report["failure_code"] == "PUBLISHABILITY_BLOCKED"
    assert report["cleanup"]["reason"] == "audit_only_no_worktree"


def test_writer_publish_service_writes_run_scoped_report(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "writer_run_demo"
    run_dir.mkdir()
    publish_root = tmp_path / ".cache" / "sqlite_kb" / "reports" / "writer_publish" / run_dir.name
    publish_root.mkdir(parents=True)

    monkeypatch.setattr(
        writer_publish_service,
        "namespace_from_args",
        lambda args, root: (run_dir, "publishable", False, False, False),
    )
    monkeypatch.setattr(
        writer_publish_service,
        "run_publish_from_run",
        lambda **kwargs: {
            "status": "no_changes",
            "mode": "publishable",
            "run_dir": str(run_dir),
            "publish_root": str(publish_root),
            "repo_root": str(tmp_path / "guidelines_repo"),
            "db_path": str(publish_root / "writer_publish.sqlite"),
            "failure_code": "NO_CHANGES",
            "failure_message": "export completed but produced no git diff",
            "cleanup": {
                "requested": True,
                "performed": False,
                "reason": "preserved_after_non_pass",
            },
            "export_delta": {
                "manifest_path": str(publish_root / "exported_guidelines_changes.json"),
                "note_path": str(publish_root / "exported_guidelines" / "THIS_RUN_CHANGES.md"),
            },
            "commit": {"committed": False},
            "push": {"pushed": False},
        },
    )
    (publish_root / "exported_guidelines").mkdir(parents=True)
    (publish_root / "exported_guidelines" / "THIS_RUN_CHANGES.md").write_text(
        "# This Run Changes\n", encoding="utf-8"
    )
    (publish_root / "exported_guidelines_changes.json").write_text("{}\n", encoding="utf-8")

    code = writer_publish_service.run(Namespace(output=""), root=tmp_path)

    report_path = publish_root / "writer_publish_report.json"
    assert code == 0
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["export_delta"]["manifest_path"].endswith("exported_guidelines_changes.json")
