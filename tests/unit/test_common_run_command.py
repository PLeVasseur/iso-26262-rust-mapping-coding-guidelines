from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from _common import RUN_COMMAND_TIMEOUT_RETURN_CODE, run_command  # noqa: E402


class RunCommandTests(unittest.TestCase):
    def test_run_command_returns_timeout_result(self) -> None:
        completed = run_command(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout_seconds=0.1,
        )
        self.assertEqual(completed.returncode, RUN_COMMAND_TIMEOUT_RETURN_CODE)
        self.assertIn("timeout", completed.stderr.lower())

    def test_run_command_success(self) -> None:
        completed = run_command(
            [sys.executable, "-c", "print('ok')"],
            timeout_seconds=1,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
