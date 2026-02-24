from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.services import verify_service  # noqa: E402


class VerifyOperationTagTests(unittest.TestCase):
    def test_verify_service_forwards_operation_tag(self) -> None:
        args = Namespace(corpus="rust_reference", extra_args=[], profile_path="")

        with patch.object(verify_service, "run_main", return_value=0) as run_main_mock:
            status = verify_service.run(args, root=ROOT)

        self.assertEqual(status, 0)
        forwarded_argv = run_main_mock.call_args.args[1]
        self.assertIn("--operation", forwarded_argv)
        self.assertIn("verify", forwarded_argv)


if __name__ == "__main__":
    unittest.main()
