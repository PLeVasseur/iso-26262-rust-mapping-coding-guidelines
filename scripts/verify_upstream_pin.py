"""Verify guidelines repo checkout matches pinned SHAs."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

PIN_FILE = Path(".upstream-pin.json")

TRACKED_FILES = [
    "scripts/common/guideline_templates.py",
    "scripts/extract_rust_examples.py",
    "scripts/rustdoc_utils.py",
    "make.py",
    "src/spec.lock",
    "src/examples_prelude.rs",
    "exts/coding_guidelines/__init__.py",
]


def compute_hashes(repo_root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel_path in TRACKED_FILES:
        full_path = repo_root / rel_path
        if full_path.exists():
            hashes[rel_path] = hashlib.sha256(full_path.read_bytes()).hexdigest()
        else:
            hashes[rel_path] = "MISSING"
    return hashes


def create_pin(repo_root: Path) -> None:
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip()
    pin = {"commit_sha": sha, "file_hashes": compute_hashes(repo_root)}
    PIN_FILE.write_text(json.dumps(pin, indent=2), encoding="utf-8")
    print(f"Pin created: {sha}")


def verify_pin(repo_root: Path, exemplars_only: bool = False) -> bool:
    if not PIN_FILE.exists():
        print("ERROR: No pin file. Run with --create first.")
        return False

    if exemplars_only:
        return verify_exemplar_manifest(repo_root)

    pin = json.loads(PIN_FILE.read_text(encoding="utf-8"))
    current_hashes = compute_hashes(repo_root)

    ok = True
    for path, expected in pin["file_hashes"].items():
        actual = current_hashes.get(path, "MISSING")
        if actual != expected:
            print(f"MISMATCH: {path}")
            print(f"  expected: {expected[:16]}...")
            print(f"  actual:   {actual[:16]}...")
            ok = False

    if ok:
        print(f"Pin verified: {pin['commit_sha']}")
    return ok


def verify_exemplar_manifest(repo_root: Path) -> bool:
    manifest_path = Path("data/exemplar_manifest.json")
    if not manifest_path.exists():
        print("ERROR: No exemplar manifest. Run Step 0 Part E first.")
        return False

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("exemplars", [])
    ok = True
    for entry in entries:
        rel_path = entry.get("path")
        expected_hash = entry.get("sha256")
        if not isinstance(rel_path, str) or not isinstance(expected_hash, str):
            print(f"INVALID_ENTRY: {entry}")
            ok = False
            continue

        full_path = repo_root / rel_path
        if not full_path.exists():
            print(f"MISSING: {rel_path}")
            ok = False
            continue

        actual_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            print(f"DRIFT: {rel_path}")
            print(f"  expected: {expected_hash[:16]}...")
            print(f"  actual:   {actual_hash[:16]}...")
            ok = False

    if ok:
        print(f"Exemplar manifest verified: {len(entries)} files")
    return ok


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("repo_root", type=Path)
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--exemplars-only", action="store_true")
    args = parser.parse_args()

    if args.create:
        create_pin(args.repo_root)
        return

    if args.exemplars_only:
        if not verify_exemplar_manifest(args.repo_root):
            sys.exit(1)
        return

    if not verify_pin(args.repo_root):
        sys.exit(1)


if __name__ == "__main__":
    main()
