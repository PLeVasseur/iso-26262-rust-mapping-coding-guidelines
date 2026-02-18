#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from _common import EXIT_RUNTIME_FAIL, EXIT_SUCCESS, repo_root, run_command, utc_now, write_yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Snapshot rustup offline docs metadata")
    parser.add_argument("--toolchain", required=True)
    parser.add_argument(
        "--copy-html", action="store_true", help="Copy local rust docs HTML into docs_snapshots"
    )
    parser.add_argument(
        "--skip-install", action="store_true", help="Skip rustup install/component steps"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = repo_root()

    if not shutil.which("rustup"):
        print("[rust-snapshot][error] rustup not found")
        return EXIT_RUNTIME_FAIL

    if not args.skip_install:
        for command in [
            ["rustup", "toolchain", "install", args.toolchain],
            ["rustup", "component", "add", "rust-docs", "--toolchain", args.toolchain],
            ["rustup", "component", "add", "clippy", "--toolchain", args.toolchain],
            ["rustup", "component", "add", "rustfmt", "--toolchain", args.toolchain],
        ]:
            result = run_command(command, cwd=root)
            if result.returncode != 0:
                print(f"[rust-snapshot][error] command failed: {' '.join(command)}")
                print(result.stderr)
                return EXIT_RUNTIME_FAIL

    doc_path_result = run_command(
        ["rustup", "doc", "--path", "--toolchain", args.toolchain],
        cwd=root,
    )
    if doc_path_result.returncode != 0:
        print("[rust-snapshot][error] failed to resolve rust doc path")
        print(doc_path_result.stderr)
        return EXIT_RUNTIME_FAIL

    doc_root = Path(doc_path_result.stdout.strip())
    snapshot_dir = root / "docs_snapshots" / "rust" / args.toolchain
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    copied_to = None
    if args.copy_html:
        source_html = doc_root.parent / "html"
        target_html = snapshot_dir / "html"
        if target_html.exists():
            shutil.rmtree(target_html)
        shutil.copytree(source_html, target_html)
        copied_to = str(target_html.relative_to(root))

    manifest_path = root / "docs_snapshots" / "rust" / "manifest.yaml"
    manifest = {
        "version": 1,
        "updated_at": utc_now(),
        "toolchain": args.toolchain,
        "doc_root": str(doc_root),
        "copied_html": copied_to,
    }
    write_yaml(manifest_path, manifest)
    print(f"[rust-snapshot] wrote manifest -> {manifest_path.relative_to(root)}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
