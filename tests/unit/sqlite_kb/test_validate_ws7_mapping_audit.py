from __future__ import annotations

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


class ValidateWs7MappingAuditTests(unittest.TestCase):
    def test_generate_mapping_audit_classifies_stale_and_ranking_bug(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            guidelines_root = temp_root / "guidelines"
            rst_dir = guidelines_root / "src" / "coding-guidelines" / "expressions"
            rst_dir.mkdir(parents=True, exist_ok=True)
            stale_rst = rst_dir / "gui_stale.rst"
            stale_rst.write_text(
                "Stale Example\n============\n\n:fls: fls_missing\n\nNeeds pointer provenance.\n",
                encoding="utf-8",
            )
            ranking_rst = rst_dir / "gui_rank.rst"
            ranking_rst.write_text(
                (
                    "Pointer Example\n===============\n\n:fls: fls_live\n\n"
                    "Prefer strict provenance APIs for raw pointer manipulation.\n"
                ),
                encoding="utf-8",
            )

            manifest_path = temp_root / "heldout.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "path": "src/coding-guidelines/expressions/gui_stale.rst",
                                "acceptable_ids": ["fls_other"],
                                "provenance": {"stable_identifier": "heldout::stale"},
                            },
                            {
                                "path": "src/coding-guidelines/expressions/gui_rank.rst",
                                "acceptable_ids": ["fls_live"],
                                "provenance": {"stable_identifier": "heldout::rank"},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            publishability_audit = temp_root / "publishability.json"
            publishability_audit.write_text(json.dumps({"rows": []}), encoding="utf-8")

            db_path = temp_root / "fls_spec.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE paragraphs (
                        paragraph_id TEXT PRIMARY KEY,
                        document_link TEXT NOT NULL,
                        section_link TEXT NOT NULL,
                        clean_text TEXT NOT NULL
                    );
                    """
                )
                connection.execute(
                    "INSERT INTO paragraphs VALUES(?, ?, ?, ?)",
                    (
                        "fls_live",
                        "expressions.html",
                        "expressions.html#type-cast-expressions",
                        (
                            "Prefer strict provenance APIs for raw pointer manipulation "
                            "and address-to-pointer casts."
                        ),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            output_path = temp_root / "ws7_mapping_audit.json"
            generate_mapping_audit(
                fls_db_path=db_path,
                guidelines_root=guidelines_root,
                output_path=output_path,
                heldout_manifest_path=manifest_path,
                publishability_audit_path=publishability_audit,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            rows = {row["source_id"]: row for row in payload["rows"]}
            self.assertEqual(rows["heldout::stale"]["classification"], "stale_mapping")
            self.assertEqual(rows["heldout::rank"]["classification"], "true_ranking_bug")


if __name__ == "__main__":
    unittest.main()
