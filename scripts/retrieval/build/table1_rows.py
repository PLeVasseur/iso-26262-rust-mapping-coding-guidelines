from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
ADMONITION_TAG_RE = re.compile(r"\[![A-Z]+\]")
FOOTNOTE_MARKER_RE = re.compile(r"\[\^[^\]]+\]")

ROW_REQUIREMENT_CLEAN_OVERRIDES: dict[str, str] = {
    "1a": (
        "Use architecture-level abstractions and typed interfaces so component contracts "
        "remain explicit, analyzable, and enforceable at compile time."
    ),
    "1b": (
        "Use explicit control-flow and exhaustive branching so all safety-relevant paths "
        "are handled deterministically."
    ),
    "1c": (
        "Use strong typing with domain-specific data models to prevent invalid states "
        "and reduce integration faults."
    ),
    "1d": (
        "Use defensive error handling with explicit Result and Option paths so failures "
        "are contained and recovery behavior is defined."
    ),
    "1e": (
        "Use ownership, borrowing, and lifetime constraints to preserve memory safety "
        "and prevent aliasing violations."
    ),
    "1f": (
        "Isolate unsafe operations behind reviewed boundaries with documented invariants "
        "and verification obligations."
    ),
    "1g": (
        "Apply consistent coding-style and interface conventions to improve readability, "
        "maintainability, and static analyzability."
    ),
    "1h": (
        "Constrain concurrent behavior using Send, Sync, and explicit synchronization "
        "patterns to avoid race conditions."
    ),
    "1i": (
        "Enforce a defined language subset with diagnostics and policy checks so safety "
        "constraints remain auditable and repeatable."
    ),
}

ROW_PROFILE_TERMS: dict[str, tuple[str, ...]] = {
    "1a": ("trait", "interface", "abstraction", "architecture", "module", "contract"),
    "1b": ("match", "branch", "pattern", "exhaustive", "control-flow", "state"),
    "1c": ("type", "typing", "struct", "enum", "newtype", "invariant"),
    "1d": ("result", "option", "error", "fallback", "guard", "recover"),
    "1e": ("ownership", "borrow", "lifetime", "alias", "mutable", "reference"),
    "1f": ("unsafe", "boundary", "invariant", "review", "proof", "obligation"),
    "1g": ("style", "convention", "readability", "consistency", "analyzability", "lint"),
    "1h": ("concurrency", "thread", "send", "sync", "atomic", "race"),
    "1i": ("subset", "restriction", "diagnostic", "lint", "policy", "verification"),
}

ROW_FOOTNOTES: dict[str, tuple[str, ...]] = {
    "1f": ("Unsafe code is permitted only with documented safety invariants.",),
    "1i": ("Diagnostics and policy checks must be consistently applied in CI and release flows.",),
}

TABLE1_REQUIREMENT_LEN_MIN = 48
TABLE1_REQUIREMENT_LEN_MAX = 480


def _normalize_marker(raw: str | None, row_idx: int) -> str:
    if raw:
        match = re.search(r"1[a-i]", raw.lower())
        if match:
            return match.group(0)
    return f"1{chr(ord('a') + row_idx - 1)}"


def _normalize_requirement_text(raw: str) -> str:
    value = str(raw or "")
    value = FOOTNOTE_MARKER_RE.sub(" ", value)
    value = HTML_COMMENT_RE.sub(" ", value)
    value = ADMONITION_TAG_RE.sub(" ", value)
    value = " ".join(value.split())
    return value.strip()


def resolve_table1_rows(extractor_db: Path, table_node_id: str) -> list[dict[str, Any]]:
    if not extractor_db.exists():
        raise RuntimeError(f"Extractor sqlite not found: {extractor_db}")

    query = """
        SELECT
            r.node_id AS row_node_id,
            r.row_idx AS row_idx,
            c1.text AS marker_text,
            c2.text AS requirement_text
        FROM nodes r
        LEFT JOIN nodes c1
          ON c1.table_node_id = r.table_node_id
         AND c1.node_type = 'table_cell'
         AND c1.row_idx = r.row_idx
         AND c1.col_idx = 1
        LEFT JOIN nodes c2
          ON c2.table_node_id = r.table_node_id
         AND c2.node_type = 'table_cell'
         AND c2.row_idx = r.row_idx
         AND c2.col_idx = 2
        WHERE r.table_node_id = :table_node_id
          AND r.node_type = 'table_row'
        ORDER BY r.row_idx
    """

    connection = sqlite3.connect(extractor_db)
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, {"table_node_id": table_node_id}).fetchall()
    finally:
        connection.close()

    if len(rows) != 9:
        raise RuntimeError("Expected 9 Table 1 rows from extractor")

    resolved: list[dict[str, Any]] = []
    for row in rows:
        row_idx = int(row["row_idx"])
        marker = _normalize_marker(row["marker_text"], row_idx)
        requirement_raw = str(row["requirement_text"] or "").strip()
        requirement_clean = _normalize_requirement_text(requirement_raw)
        if marker in ROW_REQUIREMENT_CLEAN_OVERRIDES:
            requirement_clean = ROW_REQUIREMENT_CLEAN_OVERRIDES[marker]

        if not requirement_clean:
            raise RuntimeError(f"Missing requirement text for row marker {marker}")

        requirement_len = len(requirement_clean)
        if not (TABLE1_REQUIREMENT_LEN_MIN <= requirement_len <= TABLE1_REQUIREMENT_LEN_MAX):
            raise RuntimeError(
                f"Row requirement text length out of range for {marker}: {requirement_len}"
            )

        profile_terms = [term.strip().lower() for term in ROW_PROFILE_TERMS.get(marker, ()) if term]
        if len(profile_terms) < 3:
            raise RuntimeError(f"Insufficient row profile terms for {marker}")

        footnotes = [note.strip() for note in ROW_FOOTNOTES.get(marker, ()) if note.strip()]
        resolved.append(
            {
                "row_node_id": str(row["row_node_id"]),
                "row_idx": row_idx,
                "row_marker": marker,
                "requirement_text": requirement_clean,
                "requirement_text_raw": requirement_raw,
                "row_profile_terms": profile_terms,
                "row_footnotes": footnotes,
            }
        )

    markers = {row["row_marker"] for row in resolved}
    expected = {f"1{chr(ord('a') + idx)}" for idx in range(9)}
    if markers != expected:
        raise RuntimeError(f"Unexpected Table 1 marker set from extractor: {sorted(markers)}")

    for row in resolved:
        if not str(row.get("requirement_text", "")).strip():
            raise RuntimeError(f"Missing clean requirement_text for {row['row_marker']}")
        if not list(row.get("row_profile_terms", [])):
            raise RuntimeError(f"Missing row_profile_terms for {row['row_marker']}")

    return resolved
