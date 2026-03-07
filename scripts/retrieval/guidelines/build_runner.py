from __future__ import annotations

import shutil
import subprocess
import sys
import os
from pathlib import Path


def run_guidelines_build(
    *, repo_root: Path, offline: bool = True, extra_env: dict[str, str] | None = None
) -> tuple[int, str, str, list[str]]:
    cmd: list[str] = []
    make_script = repo_root / "make.py"
    if make_script.exists() and shutil.which("uv"):
        cmd = ["uv", "run", "python", str(make_script)]
    elif make_script.exists() and shutil.which("python3"):
        cmd = ["python3", str(make_script)]
    elif make_script.exists():
        cmd = [str(make_script)]
    else:
        cmd = ["./make.py"]
    if offline:
        cmd.append("--offline")

    completed = subprocess.run(
        cmd,
        cwd=str(repo_root),
        env={**os.environ, **(extra_env or {})},
        text=True,
        capture_output=True,
        check=False,
    )
    versions: list[str] = [f"python={sys.version.split()[0]}"]
    for tool in ("uv", "sphinx-build"):
        found = shutil.which(tool)
        versions.append(f"{tool}={'present' if found else 'missing'}")
    return int(completed.returncode), completed.stdout, completed.stderr, versions
