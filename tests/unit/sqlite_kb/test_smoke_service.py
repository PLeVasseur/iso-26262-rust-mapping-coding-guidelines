from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.services.capability import EXIT_UNSUPPORTED  # noqa: E402
from retrieval.services.smoke_service import run as run_smoke_service  # noqa: E402


class SmokeServiceTests(unittest.TestCase):
    def test_rust_reference_smoke_service_uses_corpus_contract_path(self) -> None:
        defaults = SimpleNamespace(
            corpus="rust_reference",
            supports_smoke=True,
            db_path=Path("/tmp/rust_reference.sqlite"),
            contract_path=Path("config/sqlite_query_contracts/rust_reference_chunk.yaml"),
            query_log_root=Path(".cache/sqlite_kb/query_logs/rust_reference"),
        )
        args = Namespace(corpus="rust_reference", extra_args=[])

        with patch(
            "retrieval.services.smoke_service.load_corpus_runtime_defaults", return_value=defaults
        ):
            with patch("retrieval.services.smoke_service.run_main", return_value=0) as run_main:
                code = run_smoke_service(args, root=ROOT)
                self.assertEqual(code, 0)
                argv = run_main.call_args.args[1]
                self.assertIn("config/sqlite_query_contracts/rust_reference_chunk.yaml", argv)

    def test_fls_spec_smoke_service_reports_unsupported(self) -> None:
        defaults = SimpleNamespace(
            corpus="fls_spec",
            supports_smoke=False,
            db_path=Path("/tmp/fls_spec.db"),
            contract_path=Path("config/sqlite_query_contracts/fls_spec.yaml"),
            query_log_root=Path(".cache/sqlite_kb/query_logs/fls_spec"),
        )
        args = Namespace(corpus="fls_spec", extra_args=[])

        with patch(
            "retrieval.services.smoke_service.load_corpus_runtime_defaults", return_value=defaults
        ):
            code = run_smoke_service(args, root=ROOT)
            self.assertEqual(code, EXIT_UNSUPPORTED)


if __name__ == "__main__":
    unittest.main()
