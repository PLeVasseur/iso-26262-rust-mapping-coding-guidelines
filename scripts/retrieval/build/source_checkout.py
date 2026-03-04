from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def run_git_command(command: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *command],
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(command)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def resolve_reference_checkout(
    *,
    reference_source_dir: Path | None,
    reference_cache_dir: Path,
    reference_repo_url: str,
    reference_revision: str | None,
    skip_fetch: bool,
) -> tuple[Path, str, str]:
    pinned_revision = str(reference_revision or "").strip()
    if not pinned_revision:
        raise RuntimeError("Reference revision is required; pass --reference-revision")

    if reference_source_dir is not None:
        source_dir = reference_source_dir.resolve()
        summary_path = source_dir / "src" / "SUMMARY.md"
        if not summary_path.exists():
            raise RuntimeError(f"Rust reference source missing src/SUMMARY.md at {source_dir}")

        if (source_dir / ".git").exists():
            commit_sha = run_git_command(["rev-parse", pinned_revision], cwd=source_dir)
            run_git_command(["checkout", "--quiet", "--detach", commit_sha], cwd=source_dir)
        else:
            commit_sha = pinned_revision

        fetched_at = _utc_now()
        return source_dir, commit_sha, fetched_at

    source_dir = reference_cache_dir.resolve()
    source_dir.parent.mkdir(parents=True, exist_ok=True)
    if not source_dir.exists():
        run_git_command(["clone", "--quiet", "--depth", "1", reference_repo_url, str(source_dir)])

    if not skip_fetch:
        run_git_command(["fetch", "--quiet", "origin"], cwd=source_dir)

    commit_sha = run_git_command(["rev-parse", pinned_revision], cwd=source_dir)
    run_git_command(["checkout", "--quiet", "--detach", commit_sha], cwd=source_dir)
    fetched_at = _utc_now()
    return source_dir, commit_sha, fetched_at
