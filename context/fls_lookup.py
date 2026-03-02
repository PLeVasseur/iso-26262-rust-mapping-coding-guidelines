"""FLS paragraph lookup from the `fls_spec` SQLite knowledge base."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FLS_DB_PATH = PROJECT_ROOT / "data" / "fls_spec.db"
GUIDELINES_REPO_ROOT = Path(
    os.environ.get(
        "GUIDELINES_REPO", "/Users/pete.levasseur/personal/safety-critical-rust-coding-guidelines"
    )
)
SPEC_LOCK_PATH = GUIDELINES_REPO_ROOT / "src" / "spec.lock"
EXEMPLAR_MANIFEST = PROJECT_ROOT / "data" / "exemplar_manifest.json"
_EXEMPLAR_OVERRIDES: list[dict[str, Any]] | None = None


def _unresolved(reason: str) -> dict[str, str]:
    return {
        "paragraph_id": "fls_UNRESOLVED",
        "text": "",
        "chapter": "",
        "section": "",
        "paragraph_number": "",
        "unresolved_reason": reason,
    }


def _fts_query(text: str) -> str:
    if " AND " in text or " OR " in text or '"' in text:
        return text.strip()
    terms = re.findall(r"[A-Za-z0-9_]+", text)
    if not terms:
        return ""
    return " OR ".join(f'"{term}"' for term in terms[:8])


def _db_has_paragraphs(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        count = int(connection.execute("SELECT COUNT(*) FROM paragraphs").fetchone()[0])
    except sqlite3.Error:
        return False
    finally:
        connection.close()
    return count > 0


def _preferred_chapters(tokens: list[str]) -> list[str]:
    lower = {token.lower() for token in tokens}
    preferred: list[str] = []

    if lower & {"send", "sync", "thread", "atomic", "fence", "ordering"}:
        preferred.append("Concurrency")
    if lower & {"unsafe", "pointer", "dereference", "undefined"}:
        preferred.append("Unsafety")
    if lower & {"trait", "implementation", "impl"}:
        preferred.append("Implementations")
    if lower & {"closure", "capture", "move"}:
        preferred.append("Expressions")

    return preferred


def _load_exemplar_overrides() -> list[dict[str, Any]]:
    global _EXEMPLAR_OVERRIDES
    if _EXEMPLAR_OVERRIDES is not None:
        return _EXEMPLAR_OVERRIDES
    if not EXEMPLAR_MANIFEST.exists():
        _EXEMPLAR_OVERRIDES = []
        return _EXEMPLAR_OVERRIDES

    payload = json.loads(EXEMPLAR_MANIFEST.read_text(encoding="utf-8"))
    rows = payload.get("exemplars") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        rows = []

    overrides: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rel_path = str(row.get("path", "")).strip()
        if not rel_path:
            continue
        rst_path = GUIDELINES_REPO_ROOT / rel_path
        if not rst_path.exists():
            continue
        text = rst_path.read_text(encoding="utf-8")
        title_match = re.search(r"^(.+)\n=+\n", text, re.MULTILINE)
        fls_match = re.search(r":fls:\s+(fls_[A-Za-z0-9_]+)", text)
        if not title_match or not fls_match:
            continue
        tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]+", title_match.group(1))
            if len(token) > 2
        }
        if not tokens:
            continue
        overrides.append(
            {
                "tokens": tokens,
                "paragraph_id": fls_match.group(1),
                "title": title_match.group(1).strip(),
            }
        )

    _EXEMPLAR_OVERRIDES = overrides
    return _EXEMPLAR_OVERRIDES


def _match_exemplar_override(construct_terms: list[str]) -> str:
    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_]+", " ".join(construct_terms))
        if len(token) > 2
    }
    if not tokens:
        return ""
    best_id = ""
    best_score = 0.0
    for entry in _load_exemplar_overrides():
        exemplar_tokens = entry.get("tokens")
        if not isinstance(exemplar_tokens, set) or not exemplar_tokens:
            continue
        overlap = len(tokens & exemplar_tokens)
        if overlap == 0:
            continue
        score = overlap / float(len(exemplar_tokens))
        if score > best_score:
            best_score = score
            best_id = str(entry.get("paragraph_id", ""))
    return best_id if best_score >= 0.5 else ""


def _fetch_paragraph(paragraph_id: str, db_path: Path) -> dict[str, str] | None:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT paragraph_id, text, chapter, section, paragraph_number
            FROM paragraphs
            WHERE paragraph_id = ?
            LIMIT 1
            """,
            (paragraph_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return {
        "paragraph_id": str(row["paragraph_id"]),
        "text": str(row["text"]),
        "chapter": str(row["chapter"]),
        "section": str(row["section"]),
        "paragraph_number": str(row["paragraph_number"]),
    }


def search_fls_paragraphs(
    query: str,
    *,
    db_path: Path = FLS_DB_PATH,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []

    fts_query = _fts_query(query)
    if not fts_query:
        return []

    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT
                p.paragraph_id,
                p.paragraph_number,
                p.chapter,
                p.section,
                p.text,
                p.source_file,
                paragraphs_fts.rank AS bm25_rank
            FROM paragraphs_fts
            JOIN paragraphs AS p ON paragraphs_fts.rowid = p.rowid
            WHERE paragraphs_fts MATCH ?
            ORDER BY paragraphs_fts.rank ASC
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _search_fls_paragraphs_in_chapter(
    query: str,
    *,
    chapter: str,
    db_path: Path,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []

    fts_query = _fts_query(query)
    if not fts_query:
        return []

    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT
                p.paragraph_id,
                p.paragraph_number,
                p.chapter,
                p.section,
                p.text,
                p.source_file,
                paragraphs_fts.rank AS bm25_rank
            FROM paragraphs_fts
            JOIN paragraphs AS p ON paragraphs_fts.rowid = p.rowid
            WHERE paragraphs_fts MATCH ? AND p.chapter = ?
            ORDER BY paragraphs_fts.rank ASC
            LIMIT ?
            """,
            (fts_query, chapter, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def validate_fls_id(paragraph_id: str, *, spec_lock_path: Path = SPEC_LOCK_PATH) -> bool:
    if not spec_lock_path.exists():
        return True

    try:
        lock_data = json.loads(spec_lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True

    for document in lock_data.get("documents", []):
        for section in document.get("sections", []):
            if section.get("id") == paragraph_id:
                return True
            for paragraph in section.get("paragraphs", []):
                if paragraph.get("id") == paragraph_id:
                    return True
    return False


def resolve_fls_for_construct(
    construct_terms: list[str],
    *,
    db_path: Path = FLS_DB_PATH,
    spec_lock_path: Path = SPEC_LOCK_PATH,
) -> dict[str, str]:
    if not _db_has_paragraphs(db_path):
        raise RuntimeError(
            "FLS DB unavailable or empty; build data/fls_spec.db before FLS resolution."
        )

    terms = [term.strip() for term in construct_terms if term.strip()]
    if not terms:
        return _unresolved("no construct terms provided")

    exemplar_override = _match_exemplar_override(terms)
    if exemplar_override and validate_fls_id(exemplar_override, spec_lock_path=spec_lock_path):
        paragraph = _fetch_paragraph(exemplar_override, db_path)
        if paragraph is not None:
            return paragraph

    tokenized: list[str] = []
    for term in terms[:5]:
        tokenized.extend(re.findall(r"[A-Za-z0-9_]+", term))
    expanded: list[str] = []
    for token in tokenized:
        expanded.append(token)
        lowered = token.lower()
        if lowered == "implementation":
            expanded.extend(["impl", "implementations"])
        elif lowered == "trait":
            expanded.append("traits")
        elif lowered == "closure":
            expanded.append("closures")
    tokenized = expanded
    tokenized = tokenized[:8]

    strict_query = " AND ".join(f'"{token}"' for token in tokenized)
    broad_query = " OR ".join(f'"{token}"' for token in tokenized)

    results = search_fls_paragraphs(strict_query, db_path=db_path, limit=10)
    if not results:
        results = search_fls_paragraphs(broad_query, db_path=db_path, limit=10)
    if not results:
        for term in terms:
            results = search_fls_paragraphs(term, db_path=db_path, limit=10)
            if results:
                break

    if not results:
        return _unresolved(f"no FLS match for terms: {terms}")

    best = results[0]
    preferred_chapters = _preferred_chapters(tokenized)
    if preferred_chapters:
        preferred_set = set(preferred_chapters)
        for candidate in results:
            if str(candidate.get("chapter", "")) in preferred_set:
                best = candidate
                break
        else:
            for chapter in preferred_chapters:
                chapter_results = _search_fls_paragraphs_in_chapter(
                    broad_query,
                    chapter=chapter,
                    db_path=db_path,
                    limit=3,
                )
                if chapter_results:
                    best = chapter_results[0]
                    break
    paragraph_id = str(best.get("paragraph_id", ""))
    if not paragraph_id:
        return _unresolved("search returned no paragraph_id")

    if not validate_fls_id(paragraph_id, spec_lock_path=spec_lock_path):
        unresolved = _unresolved(f"ID {paragraph_id} not present in current spec.lock")
        unresolved["stale_candidate"] = paragraph_id
        return unresolved

    return {
        "paragraph_id": paragraph_id,
        "text": str(best.get("text", "")),
        "chapter": str(best.get("chapter", "")),
        "section": str(best.get("section", "")),
        "paragraph_number": str(best.get("paragraph_number", "")),
    }


def get_fls_db_stats(db_path: Path = FLS_DB_PATH) -> dict[str, Any]:
    if not db_path.exists():
        return {"available": False, "source": "none"}

    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        paragraph_count = int(connection.execute("SELECT COUNT(*) FROM paragraphs").fetchone()[0])
        chapter_count = int(
            connection.execute("SELECT COUNT(DISTINCT chapter) FROM paragraphs").fetchone()[0]
        )
        latest = connection.execute(
            """
            SELECT commit_sha, built_at
            FROM snapshots
            ORDER BY snapshot_id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()

    stats: dict[str, Any] = {
        "available": paragraph_count > 0,
        "source": "fls_spec_db" if paragraph_count > 0 else "none",
        "paragraph_count": paragraph_count,
        "chapter_count": chapter_count,
    }
    if latest:
        stats["commit_sha"] = latest[0]
        stats["built_at"] = latest[1]
    return stats
