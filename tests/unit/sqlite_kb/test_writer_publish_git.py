from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from retrieval.writer_host import publish_git


def _cp(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_finalize_commit_no_changes(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, cwd: Path):
        calls.append(args)
        if args[:3] == ("git", "add", "-A"):
            return _cp(0)
        if args[:4] == ("git", "diff", "--cached", "--quiet"):
            return _cp(0)
        return _cp(1, stderr="unexpected")

    monkeypatch.setattr(publish_git, "_run", fake_run)
    result = publish_git.finalize_commit(worktree_root=tmp_path, message="test")
    assert result["committed"] is False
    assert any(call[:3] == ("git", "add", "-A") for call in calls)


def test_push_branch_raises_on_failure(monkeypatch, tmp_path: Path) -> None:
    def fake_run(*args: str, cwd: Path):
        return _cp(1, stderr="push denied")

    monkeypatch.setattr(publish_git, "_run", fake_run)
    with pytest.raises(RuntimeError):
        publish_git.push_branch(worktree_root=tmp_path, branch="demo")
