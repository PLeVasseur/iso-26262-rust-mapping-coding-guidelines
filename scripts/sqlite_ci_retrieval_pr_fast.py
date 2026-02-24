#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def _run(command: list[str], root: Path) -> None:
    completed = subprocess.run(command, cwd=root, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    commands = [
        ["uv", "run", "ruff", "check", "scripts", "tests/unit/sqlite_kb"],
        [
            "uv",
            "run",
            "python",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests/unit/sqlite_kb",
            "-p",
            "test_*.py",
        ],
        [
            "uv",
            "run",
            "python",
            "scripts/sqlite_kb.py",
            "smoke",
            "--corpus",
            "rust_reference",
            "--no-build-if-missing",
        ],
    ]

    try:
        for command in commands:
            _run(command, root=root)
    except RuntimeError as exc:
        print(f"[ci-retrieval-pr-fast][error] {exc}")
        return EXIT_RUNTIME_FAIL

    print("[ci-retrieval-pr-fast][ok] lexical/queryability checks passed")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
