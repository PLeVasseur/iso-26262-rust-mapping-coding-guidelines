"""Load stdlib type index from core_docs SQLite DB."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_DOCS_DB_PATH = PROJECT_ROOT / "data" / "core_docs.db"

KNOWN_STD_TYPES: dict[str, str] = {
    "Option": "core::option::Option",
    "Result": "core::result::Result",
    "Vec": "alloc::vec::Vec",
    "String": "alloc::string::String",
    "Box": "alloc::boxed::Box",
    "Pin": "core::pin::Pin",
    "Arc": "alloc::sync::Arc",
    "Mutex": "std::sync::Mutex",
    "AtomicBool": "core::sync::atomic::AtomicBool",
    "AtomicUsize": "core::sync::atomic::AtomicUsize",
    "fence": "core::sync::atomic::fence",
    "Ordering": "core::sync::atomic::Ordering",
    "Drop": "core::ops::Drop",
    "Deref": "core::ops::Deref",
    "Future": "core::future::Future",
    "Poll": "core::task::Poll",
    "Waker": "core::task::Waker",
}


def load_stdlib_index(db_path: Path = CORE_DOCS_DB_PATH) -> dict[str, str]:
    """Load short-name -> fq_path mapping from core_docs DB."""
    if not db_path.exists():
        return dict(KNOWN_STD_TYPES)

    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = connection.execute(
            """
            SELECT fq_path
            FROM items
            ORDER BY fq_path
            """
        )
        lookup: dict[str, str] = {}
        for (fq_path,) in cursor:
            path = str(fq_path)
            short_name = path.rsplit("::", 1)[-1] if "::" in path else path
            if short_name not in lookup:
                lookup[short_name] = path
        connection.close()
        return lookup if lookup else dict(KNOWN_STD_TYPES)
    except sqlite3.Error:
        return dict(KNOWN_STD_TYPES)


def validate_std_path(candidate_path: str, db_path: Path = CORE_DOCS_DB_PATH) -> bool:
    """Check whether a fully-qualified stdlib path exists."""
    if not db_path.exists():
        return candidate_path in KNOWN_STD_TYPES.values()

    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = connection.execute(
            "SELECT 1 FROM items WHERE fq_path = ? LIMIT 1",
            (candidate_path,),
        )
        found = cursor.fetchone() is not None
        connection.close()
        return found
    except sqlite3.Error:
        return candidate_path in KNOWN_STD_TYPES.values()


def extract_stdlib_json(db_path: Path, output_path: Path) -> None:
    """One-time extraction helper for offline JSON lookup."""
    payload = load_stdlib_index(db_path)
    output_path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
