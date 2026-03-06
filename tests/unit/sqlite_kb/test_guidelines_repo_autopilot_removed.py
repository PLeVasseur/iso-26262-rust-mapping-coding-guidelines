from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import sqlite_kb  # noqa: E402


def test_guidelines_repo_autopilot_parser_removed() -> None:
    with patch.object(
        sys,
        "argv",
        ["sqlite_kb.py", "guidelines-repo", "autopilot", "--profile", "fast"],
    ):
        with pytest.raises(SystemExit):
            sqlite_kb.parse_args()
