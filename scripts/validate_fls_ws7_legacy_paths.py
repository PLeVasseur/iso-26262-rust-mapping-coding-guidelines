"""Audit legacy FLS heuristic path quarantine for WS7."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    PROJECT_ROOT / ".cache" / "sqlite_kb" / "reports" / "fls_spec" / "ws7_legacy_path_audit.json"
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run_audit() -> dict[str, object]:
    fls_lookup = _read_text(PROJECT_ROOT / "context" / "fls_lookup.py")
    fls_search_runtime = _read_text(PROJECT_ROOT / "context" / "fls_search_runtime.py")
    candidate_search = _read_text(
        PROJECT_ROOT / "scripts" / "retrieval" / "writer_host" / "fls_candidate_search.py"
    )
    return {
        "status": "pass",
        "checks": {
            "lookup_uses_ws7_runtime": "resolve_ws7_guideline" in fls_lookup,
            "lookup_avoids_legacy_search_runtime": "search_fls_paragraphs(" not in fls_lookup,
            "lookup_avoids_legacy_candidate_helper": "gather_candidates(" not in fls_lookup,
            "legacy_search_runtime_retired": "legacy compatibility helper is retired"
            in fls_search_runtime,
            "legacy_candidate_helper_retired": "legacy compatibility helper is retired"
            in candidate_search,
        },
    }


def write_report(report: dict[str, object], *, output_path: Path | None = None) -> Path:
    out = output_path or DEFAULT_OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit retired WS7 legacy helper paths")
    parser.add_argument("--output", default="", help="Optional explicit output path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(str(args.output).strip()).resolve() if str(args.output).strip() else None
    report = run_audit()
    out = write_report(report, output_path=output)
    print(out)
    return 0 if all(bool(value) for value in dict(report.get("checks", {})).values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
