from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import sqlite_kb  # noqa: E402


class GuidelinesRepoCapabilityContractTests(unittest.TestCase):
    def _run(self, subcommand: str) -> tuple[int, str]:
        stdout = io.StringIO()
        argv = ["sqlite_kb.py", subcommand, "--corpus", "guidelines_repo"]
        with patch.object(sys, "argv", argv):
            with redirect_stdout(stdout):
                try:
                    status = sqlite_kb.main()
                except SystemExit as exc:
                    status = int(exc.code or 0)
        return int(status), stdout.getvalue()

    def test_query_and_eval_are_disabled(self) -> None:
        for subcommand in ("query", "eval", "eval-report"):
            status, output = self._run(subcommand)
            self.assertEqual(status, 4)
            payload = json.loads(output.strip().splitlines()[0])
            self.assertEqual(payload.get("status"), "unsupported_operation")
            self.assertEqual(payload.get("corpus"), "guidelines_repo")


if __name__ == "__main__":
    unittest.main()
