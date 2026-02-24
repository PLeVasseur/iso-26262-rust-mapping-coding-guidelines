from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TIMESTAMP_TOKEN = re.compile(r"\d{8}T\d{6}Z")


class NoTimestampedDocsArtifactsTests(unittest.TestCase):
    def test_docs_markdown_filenames_do_not_contain_run_timestamps(self) -> None:
        docs_root = ROOT / "docs"
        offenders: list[str] = []
        for file_path in sorted(docs_root.glob("**/*.md")):
            if TIMESTAMP_TOKEN.search(file_path.name):
                offenders.append(str(file_path.relative_to(ROOT)))

        self.assertEqual(
            offenders,
            [],
            msg=(
                "timestamped markdown artifacts are not allowed in docs/: " + ", ".join(offenders)
            ),
        )


if __name__ == "__main__":
    unittest.main()
