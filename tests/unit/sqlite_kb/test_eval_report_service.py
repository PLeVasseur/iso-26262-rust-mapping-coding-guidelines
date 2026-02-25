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

from retrieval.services import eval_report_service  # noqa: E402


class EvalReportServiceTests(unittest.TestCase):
    def test_service_forwards_defaults_and_extra_args(self) -> None:
        args = Namespace(
            corpus="core_docs",
            profile_path="",
            extra_args=["--eval-path", "foo/eval.json", "--top-n", "5"],
        )
        with patch.object(eval_report_service, "run_main", return_value=0) as run_main_mock:
            status = eval_report_service.run(args, root=ROOT)

        self.assertEqual(status, 0)
        forwarded_argv = run_main_mock.call_args.args[1]
        self.assertIn("--corpus", forwarded_argv)
        self.assertIn("core_docs", forwarded_argv)
        self.assertIn("--db-path", forwarded_argv)
        self.assertIn("--testset-path", forwarded_argv)
        self.assertIn("--report-root", forwarded_argv)
        self.assertIn("--eval-path", forwarded_argv)
        self.assertIn("foo/eval.json", forwarded_argv)


if __name__ == "__main__":
    unittest.main()
