from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
THIS_DIR = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from _fixture import create_reference_fixture  # noqa: E402

from sqlite_build_rust_reference import DEFAULT_EXTRACTOR_DB  # noqa: E402
from sqlite_smoke_rust_reference import run_smoke  # noqa: E402


class SmokeRustReferenceTests(unittest.TestCase):
    def test_smoke_passes_with_autobuild(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = temp_root / "current" / "rust_reference.sqlite"
            snapshot_root = temp_root / "snapshots"
            manifest_path = temp_root / "manifest.yaml"
            query_log_root = temp_root / "query_logs"
            contract_path = ROOT / "config" / "sqlite_query_contracts" / "rust_reference.yaml"
            reference_source_dir = create_reference_fixture(temp_root)

            ok, message = run_smoke(
                db_path=db_path,
                contract_path=contract_path,
                query_log_root=query_log_root,
                snapshot_root=snapshot_root,
                manifest_path=manifest_path,
                extractor_db=DEFAULT_EXTRACTOR_DB,
                build_if_missing=True,
                reference_source_dir=reference_source_dir,
                reference_cache_dir=temp_root / "source-cache",
                reference_repo_url="unused-for-fixture",
                reference_revision="fixture-001",
                skip_fetch=True,
                min_sections=4,
                min_statements=8,
                min_mechanisms=4,
            )

            self.assertTrue(ok)
            self.assertIn("passed", message)


if __name__ == "__main__":
    unittest.main()
