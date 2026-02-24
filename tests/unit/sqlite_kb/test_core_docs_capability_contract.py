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


class CoreDocsCapabilityContractTests(unittest.TestCase):
    def _run(self, subcommand: str) -> tuple[int, str]:
        stdout = io.StringIO()
        with patch.object(
            sys,
            "argv",
            ["sqlite_kb.py", subcommand, "--corpus", "core_docs"],
        ):
            with redirect_stdout(stdout):
                status = sqlite_kb.main()
        return int(status), stdout.getvalue()

    def test_phase_a_disabled_operations_emit_typed_unsupported_payload(self) -> None:
        disabled = ("build", "materialize", "smoke", "capture", "verify", "validate")
        for subcommand in disabled:
            status, output = self._run(subcommand)
            self.assertEqual(status, 4, msg=f"expected unsupported exit for {subcommand}")
            payload = json.loads(output.strip().splitlines()[0])
            self.assertEqual(payload.get("status"), "unsupported_operation")
            self.assertEqual(payload.get("corpus"), "core_docs")
            self.assertEqual(payload.get("operation"), subcommand)

    def test_phase_a_enabled_operations_are_not_marked_unsupported(self) -> None:
        for subcommand in ("query", "eval", "migrate"):
            status, _ = self._run(subcommand)
            self.assertNotEqual(status, 4, msg=f"{subcommand} should be enabled in phase A")


if __name__ == "__main__":
    unittest.main()
