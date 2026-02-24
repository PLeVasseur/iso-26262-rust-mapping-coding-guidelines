from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class NoTopLevelRustOrchestrationTests(unittest.TestCase):
    def test_no_rust_reference_top_level_scripts(self) -> None:
        scripts_root = ROOT / "scripts"
        rust_scripts = sorted(scripts_root.glob("sqlite_*rust_reference*.py"))
        self.assertEqual(rust_scripts, [])

    def test_no_top_level_import_edges_to_removed_modules(self) -> None:
        deny_from = re.compile(r"from\s+sqlite_.*rust_reference")
        deny_import = re.compile(r"import\s+sqlite_.*rust_reference")

        for file_path in sorted((ROOT / "scripts").glob("**/*.py")):
            rel = file_path.relative_to(ROOT)
            if str(rel).startswith("scripts/retrieval/corpora/"):
                continue
            text = file_path.read_text(encoding="utf-8")
            self.assertIsNone(deny_from.search(text), msg=f"denylist hit in {rel}")
            self.assertIsNone(deny_import.search(text), msg=f"denylist hit in {rel}")


if __name__ == "__main__":
    unittest.main()
