#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from _common import EXIT_RUNTIME_FAIL, EXIT_SUCCESS, repo_root, utc_now, write_json, write_yaml
from build_diffset import build_diffset_bundle

VALID_VERDICTS = {"accept", "needs_change", "block"}
VALID_STATUSES = {"open", "resolved"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build/serve a diffset review page")
    parser.add_argument("--after-run")
    parser.add_argument("--before-run")
    parser.add_argument("--diffset-id", help="Load an existing diffset bundle")
    parser.add_argument("--output-root", type=Path, default=Path(".cache/reviews/diffsets"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def normalize_review_payload(payload: dict, diffset_id: str) -> dict:
    reviewer = str(payload.get("reviewer") or "unknown").strip() or "unknown"
    reviewed_at = str(payload.get("reviewed_at") or utc_now()).strip() or utc_now()

    raw_items = payload.get("items") or []
    if isinstance(raw_items, dict):
        raw_items = list(raw_items.values())
    if not isinstance(raw_items, list):
        raw_items = []

    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("item_id") or "").strip()
        verdict = str(raw.get("verdict") or "").strip()
        if not item_id or verdict not in VALID_VERDICTS:
            continue

        status = str(raw.get("status") or "").strip().lower()
        if status not in VALID_STATUSES:
            status = "resolved" if verdict == "accept" else "open"

        items.append(
            {
                "item_id": item_id,
                "verdict": verdict,
                "comment": str(raw.get("comment") or ""),
                "status": status,
                "updated_at": str(raw.get("updated_at") or reviewed_at),
            }
        )

    items.sort(key=lambda value: value["item_id"])
    return {
        "version": 1,
        "diffset_id": diffset_id,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "items": items,
    }


def send_json_response(handler: SimpleHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def load_json_body(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        return {}
    return payload


def make_handler(bundle_dir: Path, root: Path):
    class ReviewHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(bundle_dir), **kwargs)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/review_state":
                state_path = bundle_dir / "review_state.json"
                if state_path.exists():
                    payload = json.loads(state_path.read_text(encoding="utf-8"))
                else:
                    payload = {
                        "version": 1,
                        "diffset_id": bundle_dir.name,
                        "reviewer": "",
                        "reviewed_at": None,
                        "items": [],
                    }
                send_json_response(self, 200, payload)
                return

            if parsed.path in {"/", ""}:
                self.path = "/review.html"
            super().do_GET()

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                payload = load_json_body(self)
            except Exception as exc:  # pragma: no cover - malformed client payload
                send_json_response(
                    self,
                    400,
                    {"ok": False, "error": f"invalid JSON payload: {exc}"},
                )
                return

            if parsed.path == "/api/review_state":
                manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
                normalized = normalize_review_payload(payload, manifest["diffset_id"])
                write_json(bundle_dir / "review_state.json", normalized)
                send_json_response(self, 200, {"ok": True, "saved": "review_state.json"})
                return

            if parsed.path == "/api/export_feedback":
                manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
                normalized = normalize_review_payload(payload, manifest["diffset_id"])
                feedback_path = (
                    root / "feedback" / "diffset_reviews" / f"{manifest['diffset_id']}.yaml"
                )
                write_yaml(feedback_path, normalized)
                write_json(bundle_dir / "review_state.json", normalized)
                send_json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "feedback_path": str(feedback_path.relative_to(root)),
                    },
                )
                return

            send_json_response(self, 404, {"ok": False, "error": "not found"})

    return ReviewHandler


def try_open_browser(url: str) -> bool:
    try:
        if webbrowser.open(url, new=2):
            return True
    except Exception:
        pass

    launchers: list[list[str]] = [
        ["xdg-open", url],
        ["open", url],
    ]
    if sys.platform.startswith("win"):
        launchers.append(["cmd", "/c", "start", "", url])

    for command in launchers:
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            continue

    return False


def resolve_bundle(args: argparse.Namespace, root: Path) -> Path:
    if args.diffset_id:
        bundle = root / args.output_root / args.diffset_id
        if not bundle.exists():
            raise FileNotFoundError(f"diffset not found: {bundle.relative_to(root)}")
        return bundle

    if not args.after_run:
        raise ValueError("--after-run is required when --diffset-id is not provided")

    bundle, _manifest, _items = build_diffset_bundle(
        root,
        after_run_id=args.after_run,
        before_run_id=args.before_run,
        output_root=args.output_root,
    )
    return bundle


def main() -> int:
    args = parse_args()
    root = repo_root()

    try:
        bundle_dir = resolve_bundle(args, root)
    except Exception as exc:
        print(f"[review][error] {exc}")
        return EXIT_RUNTIME_FAIL

    manifest_path = bundle_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if args.once:
        target = (bundle_dir / "review.html").as_uri()
        print(f"[review] diffset_id={manifest['diffset_id']}")
        print(f"[review] review_page={target}")
        if not args.no_open:
            opened = try_open_browser(target)
            if not opened:
                print("[review][warn] failed to launch browser automatically")
        return EXIT_SUCCESS

    handler = make_handler(bundle_dir, root)
    try:
        server = ThreadingHTTPServer((args.host, args.port), handler)
    except OSError as exc:
        print(f"[review][error] failed to start server on {args.host}:{args.port} ({exc})")
        return EXIT_RUNTIME_FAIL
    url = f"http://{args.host}:{args.port}/"

    print(f"[review] diffset_id={manifest['diffset_id']}")
    print(f"[review] serving bundle -> {bundle_dir.relative_to(root)}")
    print(f"[review] url={url}")

    if not args.no_open:
        opened = try_open_browser(url)
        if not opened:
            print("[review][warn] failed to launch browser automatically")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[review] shutting down")
    finally:
        server.server_close()

    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
