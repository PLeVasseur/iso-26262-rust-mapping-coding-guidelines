from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from validate_ws7_mapping_audit import generate_mapping_audit  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MappingRefreshNonRegressionTests(unittest.TestCase):
    def test_guidelines_only_mapping_refresh_does_not_mutate_retrieval_dbs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            guidelines_root = temp_root / "guidelines"
            rst_dir = guidelines_root / "src" / "coding-guidelines" / "expressions"
            rst_dir.mkdir(parents=True, exist_ok=True)
            (rst_dir / "gui_one.rst").write_text(
                "One\n===\n\n:fls: fls_live\n\nPointer provenance guidance.\n",
                encoding="utf-8",
            )

            heldout = temp_root / "heldout.json"
            heldout.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "path": "src/coding-guidelines/expressions/gui_one.rst",
                                "acceptable_ids": ["fls_live"],
                                "provenance": {"stable_identifier": "heldout::one"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            publishability = temp_root / "publishability.json"
            publishability.write_text(json.dumps({"rows": []}), encoding="utf-8")

            fls_db = temp_root / "fls_spec.db"
            rust_db = temp_root / "rust_reference.sqlite"
            core_db = temp_root / "core_docs.sqlite"
            for db_path in (fls_db, rust_db, core_db):
                connection = sqlite3.connect(db_path)
                try:
                    if db_path == fls_db:
                        connection.execute(
                            "CREATE TABLE paragraphs("
                            "paragraph_id TEXT PRIMARY KEY, document_link TEXT, "
                            "section_link TEXT, clean_text TEXT)"
                        )
                        connection.execute(
                            "INSERT INTO paragraphs VALUES(?, ?, ?, ?)",
                            (
                                "fls_live",
                                "expressions.html",
                                "expressions.html#casts",
                                "Pointer provenance guidance for raw pointer manipulation.",
                            ),
                        )
                    else:
                        connection.execute("CREATE TABLE sentinel(value TEXT)")
                        connection.execute("INSERT INTO sentinel VALUES('unchanged')")
                    connection.commit()
                finally:
                    connection.close()

            fls_before = _sha256(fls_db)
            rust_before = _sha256(rust_db)
            core_before = _sha256(core_db)
            output_path = temp_root / "ws7_mapping_audit.json"

            generate_mapping_audit(
                fls_db_path=fls_db,
                guidelines_root=guidelines_root,
                output_path=output_path,
                heldout_manifest_path=heldout,
                publishability_audit_path=publishability,
            )

            self.assertEqual(fls_before, _sha256(fls_db))
            self.assertEqual(rust_before, _sha256(rust_db))
            self.assertEqual(core_before, _sha256(core_db))
            self.assertTrue(output_path.is_file())


if __name__ == "__main__":
    unittest.main()
