from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )


def create_worktree(*, repo_root: Path, cache_root: Path) -> dict[str, str]:
    slug = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    branch = f"writer-publish-{slug}"
    worktree = (cache_root / f"worktree_{slug}").resolve()
    worktree.parent.mkdir(parents=True, exist_ok=True)
    created = _run(
        "git",
        "worktree",
        "add",
        "-b",
        branch,
        str(worktree),
        "HEAD",
        cwd=repo_root,
    )
    if created.returncode != 0:
        raise RuntimeError(
            f"failed to create worktree: {created.stderr.strip() or created.stdout.strip()}"
        )
    return {"branch": branch, "worktree": str(worktree)}


def finalize_commit(*, worktree_root: Path, message: str) -> dict[str, str | bool]:
    add = _run("git", "add", "-A", cwd=worktree_root)
    if add.returncode != 0:
        raise RuntimeError(f"git add failed: {add.stderr.strip() or add.stdout.strip()}")
    cached = _run("git", "diff", "--cached", "--quiet", cwd=worktree_root)
    if cached.returncode == 0:
        return {"committed": False, "commit": "", "message": message}
    commit = _run("git", "commit", "-m", message, cwd=worktree_root)
    if commit.returncode != 0:
        raise RuntimeError(f"git commit failed: {commit.stderr.strip() or commit.stdout.strip()}")
    sha = _run("git", "rev-parse", "HEAD", cwd=worktree_root)
    return {
        "committed": True,
        "commit": sha.stdout.strip(),
        "message": message,
    }


def push_branch(*, worktree_root: Path, branch: str) -> dict[str, str | bool]:
    pushed = _run("git", "push", "-u", "origin", branch, cwd=worktree_root)
    if pushed.returncode != 0:
        raise RuntimeError(f"git push failed: {pushed.stderr.strip() or pushed.stdout.strip()}")
    return {
        "pushed": True,
        "branch": branch,
    }


def status_porcelain(
    *, worktree_root: Path, pathspecs: list[str] | None = None
) -> list[dict[str, Any]]:
    command = ["git", "status", "--porcelain=v1"]
    if pathspecs:
        command.append("--")
        command.extend(pathspecs)
    status = _run(*command, cwd=worktree_root)
    if status.returncode != 0:
        raise RuntimeError(f"git status failed: {status.stderr.strip() or status.stdout.strip()}")
    rows: list[dict[str, Any]] = []
    for line in status.stdout.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:].strip()
        rows.append(
            {
                "code": code,
                "path": path,
                "index_status": code[:1],
                "worktree_status": code[1:2],
            }
        )
    return rows


def remove_worktree(*, repo_root: Path, worktree_root: Path) -> None:
    _run("git", "worktree", "remove", "--force", str(worktree_root), cwd=repo_root)
