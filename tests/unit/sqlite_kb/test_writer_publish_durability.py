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
        "run_ingest_from_run",
        lambda **kwargs: {"status": "pass", "annotation_policy_metrics": {}, "db": {}},
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
        "run_ingest_from_run",
        lambda **kwargs: {"status": "pass", "annotation_policy_metrics": {}, "db": {}},
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
        "run_ingest_from_run",
        lambda **kwargs: {"status": "pass", "annotation_policy_metrics": {}, "db": {}},
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
    assert removed == [(repo_root, worktree_root)]


def test_writer_publish_service_writes_run_scoped_report_and_packet(
    monkeypatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "writer_run_demo"
    run_dir.mkdir()
    (run_dir / "writer_review_packet.zip").write_bytes(b"zip")
    (run_dir / "writer_review_packet.manifest.json").write_text("{}\n", encoding="utf-8")
    publish_root = tmp_path / ".cache" / "sqlite_kb" / "reports" / "writer_publish" / run_dir.name
    publish_root.mkdir(parents=True)

    monkeypatch.setattr(
        writer_publish_service,
        "namespace_from_args",
        lambda args, root: (run_dir, "publishable", False, False),
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
    packet_path = publish_root / "writer_publish_review_packet.zip"
    manifest_path = publish_root / "writer_publish_review_packet.manifest.json"
    assert code == 0
    assert report_path.exists()
    assert packet_path.exists()
    assert manifest_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["review_packet"]["path"] == str(packet_path)
    assert payload["review_packet"]["manifest_path"] == str(manifest_path)
    assert payload["export_delta"]["manifest_path"].endswith("exported_guidelines_changes.json")
