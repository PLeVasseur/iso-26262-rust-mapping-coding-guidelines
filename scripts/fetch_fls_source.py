"""Fetch Ferrocene Language Specification RST sources from GitHub."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

FLS_REPO = "rust-lang/fls"
FLS_SRC_DIR = "src"
DEFAULT_OUTPUT_DIR = Path(".cache/fls_source/current")
DEFAULT_PIN_FILE = Path(".fls-pin")
MAX_FLS_RETRIES = 3
FLS_BACKOFF_BASE = 30


def _request_json(url: str, *, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "opencode-step6-fls-fetcher"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def _request_bytes(url: str, *, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "opencode-step6-fls-fetcher"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _resolve_target_ref(*, branch: str, pin_file: Path | None) -> str:
    if pin_file and pin_file.exists():
        pinned = pin_file.read_text(encoding="utf-8").strip()
        if pinned:
            return pinned
    return branch


def _resolve_commit_sha(*, branch: str, target_ref: str) -> str:
    try:
        ref_url = f"https://api.github.com/repos/{FLS_REPO}/git/ref/heads/{branch}"
        ref_data = _request_json(ref_url)
        return str(ref_data.get("object", {}).get("sha") or target_ref)
    except Exception:  # noqa: BLE001
        return target_ref


def _fetch_once(*, target_ref: str, output_dir: Path, branch: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    tree_url = f"https://api.github.com/repos/{FLS_REPO}/git/trees/{target_ref}?recursive=1"
    tree_data = _request_json(tree_url)
    if tree_data.get("truncated"):
        raise RuntimeError("GitHub tree response was truncated; cannot guarantee full FLS source.")

    entries = tree_data.get("tree", [])
    rst_files = sorted(
        str(entry.get("path"))
        for entry in entries
        if entry.get("type") == "blob"
        and str(entry.get("path", "")).startswith(f"{FLS_SRC_DIR}/")
        and str(entry.get("path", "")).endswith(".rst")
    )
    if not rst_files:
        raise RuntimeError("No FLS RST files found under src/ in rust-lang/fls.")

    for old in output_dir.glob("*.rst"):
        old.unlink()

    raw_base = f"https://raw.githubusercontent.com/{FLS_REPO}/{target_ref}"
    fetched: list[str] = []
    for rel_path in rst_files:
        file_url = f"{raw_base}/{rel_path}"
        file_bytes = _request_bytes(file_url)
        out_path = output_dir / Path(rel_path).name
        out_path.write_bytes(file_bytes)
        fetched.append(rel_path)

    metadata = {
        "repo": FLS_REPO,
        "ref": target_ref,
        "commit_sha": _resolve_commit_sha(branch=branch, target_ref=target_ref),
        "files_fetched": len(fetched),
        "file_list": fetched,
    }
    (output_dir / "_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def fetch_fls_source(
    *,
    branch: str = "main",
    pin_file: Path | None = DEFAULT_PIN_FILE,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    max_retries: int = MAX_FLS_RETRIES,
    backoff_base_seconds: int = FLS_BACKOFF_BASE,
) -> dict[str, Any]:
    """Fetch all FLS RST files with retry and hard-failure semantics."""
    target_ref = _resolve_target_ref(branch=branch, pin_file=pin_file)

    for attempt in range(1, max_retries + 1):
        try:
            return _fetch_once(target_ref=target_ref, output_dir=output_dir, branch=branch)
        except Exception as exc:  # noqa: BLE001
            if attempt >= max_retries:
                raise RuntimeError(
                    "FLS_FETCH_FAILURE: FLS source fetch failed after 3 retries. "
                    "Check network connectivity, GitHub API rate limits, and authentication."
                ) from exc
            wait_seconds = backoff_base_seconds * (2 ** (attempt - 1))
            print(f"FLS fetch attempt {attempt}/{max_retries} failed: {exc}")
            print(f"Retrying in {wait_seconds}s...")
            time.sleep(wait_seconds)

    raise RuntimeError("FLS_FETCH_FAILURE: unreachable retry loop exit")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--pin-file", type=Path, default=DEFAULT_PIN_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-retries", type=int, default=MAX_FLS_RETRIES)
    parser.add_argument("--backoff-base", type=int, default=FLS_BACKOFF_BASE)
    args = parser.parse_args()

    metadata = fetch_fls_source(
        branch=args.branch,
        pin_file=args.pin_file,
        output_dir=args.output_dir,
        max_retries=args.max_retries,
        backoff_base_seconds=args.backoff_base,
    )
    print(
        f"Fetched {metadata['files_fetched']} FLS files at {metadata['commit_sha']} "
        f"into {args.output_dir}"
    )


if __name__ == "__main__":
    main()
