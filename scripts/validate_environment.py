"""Validate execution environment for Step 0."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path


def check(name: str, cmd: list[str]) -> bool:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        print(f"  [FAIL] {name}: not found")
        return False
    except subprocess.TimeoutExpired:
        print(f"  [FAIL] {name}: timed out")
        return False

    if result.returncode != 0:
        print(f"  [FAIL] {name}: command failed")
        return False

    version = (result.stdout or result.stderr).strip().split("\n")[0]
    print(f"  [OK]   {name}: {version}")
    return True


def main() -> None:
    guidelines_repo = Path(
        os.environ.get(
            "GUIDELINES_REPO",
            "/Users/pete.levasseur/personal/safety-critical-rust-coding-guidelines",
        )
    )
    if not guidelines_repo.exists():
        print("  [FAIL] GUIDELINES_REPO missing")
        print("    Set GUIDELINES_REPO to the safety-critical-rust-coding-guidelines repo path")
        sys.exit(1)

    print("Environment validation:")
    ok = True
    ok &= check("Python", ["python3", "--version"])
    ok &= check("uv", ["uv", "--version"])
    ok &= check("rustc (stable)", ["rustc", "--version"])
    ok &= check("cargo", ["cargo", "--version"])

    nightly_pin = Path(".rust-nightly-pin")
    if nightly_pin.exists():
        toolchain = nightly_pin.read_text(encoding="utf-8").strip()
        ok &= check(f"rustc ({toolchain})", ["rustup", "run", toolchain, "rustc", "--version"])
        ok &= check(f"Miri ({toolchain})", ["cargo", f"+{toolchain}", "miri", "--version"])
    else:
        print("  [FAIL] Nightly pin: .rust-nightly-pin not found")
        ok = False

    try:
        response = urllib.request.urlopen("http://localhost:4096/global/health", timeout=5)
        health = json.loads(response.read().decode())
        print(f"  [OK]   OpenCode server: {health.get('version', 'unknown')}")

        request = urllib.request.Request(
            "http://localhost:4096/session",
            data=json.dumps({}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        create_resp = urllib.request.urlopen(request, timeout=10)
        session = json.loads(create_resp.read().decode())
        session_id = session["id"]
        urllib.request.urlopen(
            urllib.request.Request(f"http://localhost:4096/session/{session_id}", method="DELETE"),
            timeout=10,
        )
        print("  [OK]   OpenCode HTTP API: session create/delete OK")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] OpenCode server: {exc}")
        ok = False

    version_pin = Path(".opencode-version-pin")
    if version_pin.exists():
        expected = version_pin.read_text(encoding="utf-8").strip()
        result = subprocess.run(
            ["opencode", "--version"], capture_output=True, text=True, timeout=30
        )
        actual = result.stdout.strip()
        if actual == expected:
            print(f"  [OK]   OpenCode version: {actual} (pinned)")
        else:
            print(f"  [FAIL] OpenCode version: {actual} != pinned {expected}")
            ok = False
    else:
        print("  [FAIL] OpenCode version pin: .opencode-version-pin not found")
        ok = False

    ok &= check("Semantic backend", ["python3", "scripts/sqlite_check_semantic_backend.py"])

    if Path(".upstream-pin.json").exists():
        result = subprocess.run(
            ["python3", "scripts/verify_upstream_pin.py", str(guidelines_repo)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            print("  [OK]   Upstream pin: verified")
        else:
            print("  [FAIL] Upstream pin: mismatch")
            ok = False
    else:
        print("  [FAIL] Upstream pin: .upstream-pin.json not found")
        ok = False

    exemplar_manifest = Path("data/exemplar_manifest.json")
    if exemplar_manifest.exists():
        result = subprocess.run(
            ["python3", "scripts/verify_upstream_pin.py", str(guidelines_repo), "--exemplars-only"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            print("  [OK]   Exemplar manifest: verified")
        else:
            print("  [FAIL] Exemplar manifest: drift detected")
            ok = False
    else:
        print("  [WARN] Exemplar manifest: not yet created")

    fls_source_dir = Path(".cache/fls_source/current")
    fls_db_path = Path(".cache/sqlite_kb/current/fls_spec.db")
    if not fls_db_path.exists():
        fls_db_path = Path("data/fls_spec.db")
    source_available = fls_source_dir.exists() and any(fls_source_dir.glob("*.rst"))
    db_available = False
    if fls_db_path.exists():
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(f"file:{fls_db_path}?mode=ro", uri=True)
            db_available = conn.execute("SELECT COUNT(*) FROM paragraphs").fetchone()[0] > 0
        except sqlite3.Error:
            db_available = False
        finally:
            if conn is not None:
                conn.close()

    if source_available or db_available:
        modes = []
        if source_available:
            modes.append("source")
        if db_available:
            modes.append("db")
        print(f"  [OK]   FLS assets: available ({'+'.join(modes)})")
    else:
        print("  [WARN] FLS assets: no local source/db yet (Step 6 will fetch/build)")

    if ok:
        print("\n[OK] Environment ready")
    else:
        print("\n[FAIL] Environment has issues")
        sys.exit(1)


if __name__ == "__main__":
    main()
