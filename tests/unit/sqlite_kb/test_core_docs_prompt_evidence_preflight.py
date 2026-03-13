from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.operations.eval import _validate_required_evidence_fields  # noqa: E402


class CoreDocsPromptEvidencePreflightTests(unittest.TestCase):
    def test_preflight_requires_non_empty_mapped_field_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "core_docs.sqlite"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "CREATE TABLE core_docs_chunk_metadata("
                    "chunk_uid TEXT, item_path TEXT, target_triple TEXT)"
                )
                connection.execute("CREATE TABLE table1_rows(row_marker TEXT)")
                connection.execute(
                    "INSERT INTO core_docs_chunk_metadata("
                    "chunk_uid, item_path, target_triple"
                    ") VALUES('c1', 'core::option::Option', 'x86_64-unknown-linux-gnu')"
                )
                connection.execute("INSERT INTO table1_rows(row_marker) VALUES('1d')")
                connection.commit()
            finally:
                connection.close()

            prompts = [
                {
                    "required_evidence_fields": ["item_path", "target_triple", "row_markers"],
                }
            ]
            _validate_required_evidence_fields(db_path, prompts)


if __name__ == "__main__":
    unittest.main()
