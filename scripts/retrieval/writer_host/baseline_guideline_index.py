from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from retrieval.services.guideline_fls_resolution import get_guideline_fls_resolution_state

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_:#\-]*")
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "shall",
    "should",
    "must",
    "code",
    "rust",
}


def _tokens(*values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").lower()
        for token in _TOKEN_RE.findall(text):
            if len(token) < 4 or token in _STOPWORDS or token in seen:
                continue
            seen.add(token)
            out.append(token)
    return out


def default_baseline_db_path(*, root: Path) -> Path:
    return (root / ".cache" / "sqlite_kb" / "current" / "guidelines_repo.sqlite").resolve()


def load_baseline_guideline_index(
    *, root: Path, db_path: Path | None = None
) -> list[dict[str, Any]]:
    effective_db = (db_path or default_baseline_db_path(root=root)).resolve()
    if not effective_db.exists():
        return []
    connection = sqlite3.connect(effective_db)
    try:
        rows = connection.execute(

                "SELECT guideline_id, title, export_topic, metadata_json "
                "FROM guideline_records ORDER BY guideline_id"

        ).fetchall()
        block_rows = connection.execute(

                "SELECT guideline_id, block_type, content FROM guideline_blocks "
                "ORDER BY guideline_id, order_index"

        ).fetchall()
    finally:
        connection.close()

    blocks_by_guideline: dict[str, dict[str, list[str]]] = {}
    for guideline_id, block_type, content in block_rows:
        gid = str(guideline_id)
        block_bucket = blocks_by_guideline.setdefault(gid, {})
        block_bucket.setdefault(str(block_type), []).append(str(content or "").strip())

    index: list[dict[str, Any]] = []
    for guideline_id, title, chapter, metadata_json in rows:
        try:
            metadata = json.loads(str(metadata_json or "{}"))
        except json.JSONDecodeError:
            metadata = {}
        block_bucket = blocks_by_guideline.get(str(guideline_id), {})
        body = " ".join(block_bucket.get("body", []))
        rationale = " ".join(block_bucket.get("rationale", []))
        tags = [
            str(value).strip() for value in list(metadata.get("tags") or []) if str(value).strip()
        ]
        construct_keywords = _tokens(title, body, rationale, tags)
        review_question_hint = f"Does the code satisfy this rule: {str(title).strip()}?"
        index.append(
            {
                "guideline_id": str(guideline_id),
                "title": str(title).strip(),
                "chapter": str(chapter).strip(),
                "tags": tags,
                "operative_text": body,
                "rationale_text": rationale,
                "construct_keywords": construct_keywords,
                "review_question_hint": review_question_hint,
                "fls_id": get_guideline_fls_resolution_state(
                    str(guideline_id), db_path=effective_db
                ).get("effective_fls_id", ""),
            }
        )
    return index
