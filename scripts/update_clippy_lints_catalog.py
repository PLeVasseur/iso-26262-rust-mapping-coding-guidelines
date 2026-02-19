#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from _common import (
    EXIT_POLICY_FAIL,
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    read_yaml,
    repo_root,
    utc_now,
    write_yaml,
)

DEFAULT_URL = "https://rust-lang.github.io/rust-clippy/stable/index.html"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh Clippy stable lint catalog snapshot")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", type=Path, default=Path("data/clippy_lints_catalog.yaml"))
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the tracked catalog differs from the freshly fetched source",
    )
    return parser.parse_args()


def fetch_html(url: str, timeout: int) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "iso26262-guideline-catalog-updater/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="replace")


def extract_lint_ids(html: str) -> list[str]:
    raw_ids = re.findall(r'<article\s+id="([a-z0-9_]+)"', html)
    return sorted(set(raw_ids))


def build_payload(url: str, lint_ids: list[str]) -> dict:
    lints = [{"id": lint_id, "url": f"{url}#{lint_id}"} for lint_id in lint_ids]
    return {
        "version": 1,
        "source_url": url,
        "generated_at": utc_now(),
        "lint_count": len(lints),
        "lints": lints,
    }


def catalog_signature(payload: dict) -> tuple[str, int, tuple[tuple[str, str], ...]]:
    lints = payload.get("lints") or []
    lint_pairs: list[tuple[str, str]] = []
    for lint in lints:
        if not isinstance(lint, dict):
            continue
        lint_pairs.append((str(lint.get("id") or ""), str(lint.get("url") or "")))
    return (
        str(payload.get("source_url") or ""),
        int(payload.get("lint_count") or 0),
        tuple(sorted(lint_pairs)),
    )


def main() -> int:
    args = parse_args()
    root = repo_root()
    output_path = root / args.output

    try:
        html = fetch_html(args.url, timeout=args.timeout)
    except urllib.error.URLError as exc:
        print(f"[clippy-catalog][error] failed to fetch {args.url}: {exc}")
        return EXIT_RUNTIME_FAIL

    lint_ids = extract_lint_ids(html)
    if not lint_ids:
        print("[clippy-catalog][error] no lint ids parsed from source")
        return EXIT_RUNTIME_FAIL

    payload = build_payload(args.url, lint_ids)

    if args.check:
        if not output_path.exists():
            print(
                "[clippy-catalog][error] tracked catalog missing; run update without --check first"
            )
            return EXIT_POLICY_FAIL

        existing = read_yaml(output_path) or {}
        if catalog_signature(existing) != catalog_signature(payload):
            print("[clippy-catalog][error] tracked catalog differs from source")
            print(
                "[clippy-catalog][hint] run: uv run python scripts/update_clippy_lints_catalog.py"
            )
            return EXIT_POLICY_FAIL

        print(f"[clippy-catalog] up to date ({payload['lint_count']} lints)")
        return EXIT_SUCCESS

    write_yaml(output_path, payload)
    print(f"[clippy-catalog] wrote {output_path.relative_to(root)}")
    print(f"[clippy-catalog] lint_count={payload['lint_count']}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
