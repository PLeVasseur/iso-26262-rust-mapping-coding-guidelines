from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from controller_supervisor import build_worker_command  # noqa: E402


class ControllerSupervisorTests(unittest.TestCase):
    def test_build_worker_command_default(self) -> None:
        command = build_worker_command(
            ROOT,
            "session-1",
            spawn_command=None,
            controller_args=["--mode", "growth"],
        )
        self.assertIn("scripts/autonomous_controller.py", command)
        self.assertIn("--single-iteration", command)
        self.assertIn("--resume-session", command)
        self.assertIn("session-1", command)
        self.assertTrue(command[-2:] == ["--mode", "growth"])

    def test_build_worker_command_template_expands_controller_args(self) -> None:
        command = build_worker_command(
            ROOT,
            "session-2",
            spawn_command=(
                "python3 scripts/autonomous_controller.py --resume-session "
                "{session_id} {controller_args}"
            ),
            controller_args=["--beam-width", "6"],
        )
        self.assertIn("--resume-session", command)
        self.assertIn("session-2", command)
        self.assertIn("--beam-width", command)
        self.assertIn("6", command)
        self.assertIn("--single-iteration", command)


if __name__ == "__main__":
    unittest.main()
