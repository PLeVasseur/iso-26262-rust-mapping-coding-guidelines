from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.operations.guideline_template_bridge import parse_bibliography_payload  # noqa: E402
from retrieval.writer_host.publish_ingest import ingest_records  # noqa: E402


def test_parse_bibliography_payload_uses_source_anchor_and_author_fallback() -> None:
    payload = json.dumps(
        {
            "citation_key": "RET-ISSUE-002:SRC-4",
            "title": "Rust Reference: #[non_exhaustive] marks structs and enums.",
            "source_anchor": "https://doc.rust-lang.org/reference/attributes/type_system.html#the-non-exhaustive-attribute",
            "document": "Rust Reference",
        }
    )

    parsed = parse_bibliography_payload(payload)

    assert parsed == (
        "RET-ISSUE-002:SRC-4",
        "Rust Reference",
        "Rust Reference: #[non_exhaustive] marks structs and enums",
        "https://doc.rust-lang.org/reference/attributes/type_system.html#the-non-exhaustive-attribute",
    )


def test_ingest_records_canonicalizes_duplicate_url_descriptions(tmp_path: Path) -> None:
    db_path = tmp_path / "publish.sqlite"
    ingest_records(
        db_path=db_path,
        source_run_id="writer_run_demo",
        records=[
            {
                "guideline_id": "gui_demo",
                "chapter": "expressions",
                "filename": "gui_demo.rst",
                "title": "Demo",
                "category": "advisory",
                "status": "draft",
                "release": "1.85.1",
                "fls_id": "fls_UNRESOLVED",
                "fls_resolution": {},
                "fls_resolution_report": "",
                "publishability": {},
                "decidability": "undecidable",
                "scope": "module",
                "tags": ["strong-typing"],
                "non_compliant_miri_intent": "skip",
                "compliant_miri_intent": "check",
                "blocks": [{"block_type": "body", "order_index": 1, "content": "body"}],
                "bibliography_rows": [
                    {
                        "citation_key": "RET-ISSUE-002:SRC-4",
                        "title": "Rust Reference: #[non_exhaustive] marks structs, enums, and variants as extensible",
                        "source_anchor": "https://doc.rust-lang.org/reference/attributes/type_system.html#the-non-exhaustive-attribute",
                    },
                    {
                        "citation_key": "RET-ISSUE-002:SRC-5",
                        "title": "Rust Reference: downstream construction restrictions for #[non_exhaustive]",
                        "source_anchor": "https://doc.rust-lang.org/reference/attributes/type_system.html#the-non-exhaustive-attribute",
                    },
                ],
            }
        ],
    )

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT bib_key, content FROM guideline_bibliography ORDER BY bib_key ASC"
        ).fetchall()
        links = connection.execute(
            "SELECT guideline_id, bib_key FROM guideline_bib_links ORDER BY guideline_id ASC, bib_key ASC"
        ).fetchall()
    finally:
        connection.close()

    assert len(rows) == 1
    entry = json.loads(rows[0][1])
    assert (
        entry["url"]
        == "https://doc.rust-lang.org/reference/attributes/type_system.html#the-non-exhaustive-attribute"
    )
    assert (
        entry["title"]
        == "Rust Reference: #[non_exhaustive] marks structs, enums, and variants as extensible"
    )
    assert links == [("gui_demo", rows[0][0])]


def test_ingest_records_canonicalizes_duplicate_urls_across_guidelines(tmp_path: Path) -> None:
    db_path = tmp_path / "publish.sqlite"
    shared_url = "https://doc.rust-lang.org/reference/unsafe-keyword.html#the-unsafe-keyword"
    ingest_records(
        db_path=db_path,
        source_run_id="writer_run_demo",
        records=[
            {
                "guideline_id": "gui_one",
                "chapter": "unsafety",
                "filename": "gui_one.rst",
                "title": "One",
                "category": "advisory",
                "status": "draft",
                "release": "1.85.1",
                "fls_id": "fls_UNRESOLVED",
                "fls_resolution": {},
                "fls_resolution_report": "",
                "publishability": {},
                "decidability": "undecidable",
                "scope": "module",
                "tags": ["unsafe"],
                "non_compliant_miri_intent": "skip",
                "compliant_miri_intent": "check",
                "blocks": [{"block_type": "body", "order_index": 1, "content": "body"}],
                "bibliography_rows": [
                    {
                        "citation_key": "RR-UNSAFE-KEYWORD",
                        "title": "The Rust Reference - The unsafe keyword",
                        "source_anchor": shared_url,
                        "corpus": "rust_reference",
                    }
                ],
            },
            {
                "guideline_id": "gui_two",
                "chapter": "unsafety",
                "filename": "gui_two.rst",
                "title": "Two",
                "category": "advisory",
                "status": "draft",
                "release": "1.85.1",
                "fls_id": "fls_UNRESOLVED",
                "fls_resolution": {},
                "fls_resolution_report": "",
                "publishability": {},
                "decidability": "undecidable",
                "scope": "module",
                "tags": ["unsafe"],
                "non_compliant_miri_intent": "skip",
                "compliant_miri_intent": "check",
                "blocks": [{"block_type": "body", "order_index": 1, "content": "body"}],
                "bibliography_rows": [
                    {
                        "citation_key": "RR-UNSAFE-KEYWORD-ALT",
                        "title": "Rust Reference: unsafe keyword overview",
                        "source_anchor": shared_url,
                        "corpus": "rust_reference",
                    }
                ],
            },
        ],
    )

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT bib_key, content FROM guideline_bibliography ORDER BY bib_key ASC"
        ).fetchall()
        links = connection.execute(
            "SELECT guideline_id, bib_key FROM guideline_bib_links ORDER BY guideline_id ASC, bib_key ASC"
        ).fetchall()
    finally:
        connection.close()

    assert len(rows) == 1
    entry = json.loads(rows[0][1])
    assert entry["url"] == shared_url
    assert entry["title"] == "The Rust Reference - The unsafe keyword"
    assert entry["author"] == "rust_reference"
    assert links == [("gui_one", rows[0][0]), ("gui_two", rows[0][0])]
