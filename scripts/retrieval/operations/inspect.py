#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect guideline corpus state")
    parser.add_argument("--db-path", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db_path).expanduser().resolve()
    if not db_path.exists():
        print(json.dumps({"status": "inspect_ok", "db_path": str(db_path), "missing": True}))
        return EXIT_SUCCESS
    try:
        connection = sqlite3.connect(db_path)
        try:
            count = int(connection.execute("SELECT COUNT(*) FROM guideline_records").fetchone()[0])
        finally:
            connection.close()
    except Exception as exc:
        print(f"[inspect][error] {exc}")
        return EXIT_RUNTIME_FAIL
    print(json.dumps({"status": "inspect_ok", "db_path": str(db_path), "guidelines": count}))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
