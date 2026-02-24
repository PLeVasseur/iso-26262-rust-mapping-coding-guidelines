from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class CanonicalCliDocSyncTests(unittest.TestCase):
    def test_sqlite_kb_cli_contains_canonical_verification_command(self) -> None:
        doc_path = ROOT / "docs" / "sqlite_kb_cli.md"
        text = doc_path.read_text(encoding="utf-8")
        self.assertIn("uv run ruff check scripts tests/unit/sqlite_kb", text)
        self.assertIn(
            "uv run python -m unittest discover -s tests/unit/sqlite_kb -p 'test_*.py'",
            text,
        )


if __name__ == "__main__":
    unittest.main()
