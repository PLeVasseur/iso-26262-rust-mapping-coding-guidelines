from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class NoSyntheticCoreDocsBuildTests(unittest.TestCase):
    def test_core_docs_builder_does_not_emit_row_summary_stub_chunks(self) -> None:
        builder_path = ROOT / "scripts" / "retrieval" / "builders" / "core_docs_builder.py"
        text = builder_path.read_text(encoding="utf-8")

        deny_markers = (
            "Core docs coverage for ISO 26262 Table 1 row",
            "core-docs::",
            "Requirement:",
        )
        for marker in deny_markers:
            self.assertNotIn(marker, text, msg=f"synthetic marker present in builder: {marker}")


if __name__ == "__main__":
    unittest.main()
