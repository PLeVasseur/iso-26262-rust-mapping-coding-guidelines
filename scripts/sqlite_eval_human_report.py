#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from retrieval.corpora.registry import list_supported_corpora
from retrieval.eval.human_report import HumanReportConfig, generate_human_report
from retrieval.eval.human_report_resolvers.registry import get_human_report_resolver

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate markdown human-review report from retrieval eval.json"
    )
    parser.add_argument("--corpus", choices=list_supported_corpora(), required=True)
    parser.add_argument("--eval-path", required=True)
    parser.add_argument("--db-path", default="")
    parser.add_argument("--testset-path", default="")
    parser.add_argument("--report-root", default="")
    parser.add_argument("--output-path", default="")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--snippet-chars", type=int, default=320)
    parser.add_argument("--only-problem-prompts", action="store_true")
    parser.add_argument(
        "--allow-provenance-mismatch",
        action="store_true",
        help="Accepted for unified CLI parity; handled by sqlite_kb provenance guard",
    )
    return parser.parse_args()


def _resolve_path(root: Path, raw: str) -> Path:
    path = Path(str(raw).strip())
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]

    try:
        eval_path = _resolve_path(root, str(args.eval_path))
        if not eval_path.exists():
            raise RuntimeError(f"eval artifact not found: {eval_path}")

        db_path_raw = str(args.db_path).strip()
        if db_path_raw:
            db_path = _resolve_path(root, db_path_raw)
        else:
            payload = eval_path.read_text(encoding="utf-8")
            import json

            parsed = json.loads(payload)
            inputs = parsed.get("inputs", {}) if isinstance(parsed, dict) else {}
            eval_db_path = str(inputs.get("db_path", "")).strip()
            if not eval_db_path:
                raise RuntimeError("--db-path is required when eval.json inputs.db_path is missing")
            db_path = _resolve_path(root, eval_db_path)

        if not db_path.exists():
            raise RuntimeError(f"db file not found: {db_path}")

        testset_path = (
            _resolve_path(root, str(args.testset_path)) if str(args.testset_path).strip() else None
        )

        report_root = (
            _resolve_path(root, str(args.report_root))
            if str(args.report_root).strip()
            else (root / ".cache" / "sqlite_kb" / "reports" / str(args.corpus)).resolve()
        )
        output_path = (
            _resolve_path(root, str(args.output_path))
            if str(args.output_path).strip()
            else (
                report_root
                / f"retrieval_eval_human_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.md"
            )
        )

        resolver = get_human_report_resolver(str(args.corpus))
        config = HumanReportConfig(
            eval_path=eval_path,
            db_path=db_path,
            output_path=output_path,
            testset_path=testset_path,
            top_n=max(1, int(args.top_n)),
            snippet_chars=max(80, int(args.snippet_chars)),
            only_problem_prompts=bool(args.only_problem_prompts),
        )
        written = generate_human_report(resolver=resolver, config=config)
    except Exception as exc:  # pragma: no cover
        print(f"[sqlite_eval_human_report][error] {exc}")
        return EXIT_RUNTIME_FAIL

    print(f"[sqlite_eval_human_report] report -> {written}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
