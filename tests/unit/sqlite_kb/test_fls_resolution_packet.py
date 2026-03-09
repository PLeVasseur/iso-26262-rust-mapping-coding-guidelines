from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.fls_grounding import build_grounding_artifact  # noqa: E402
from retrieval.writer_host.fls_resolution_packet import build_resolution_packet  # noqa: E402


def _seed_prior_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE fls_documents (
                document_link TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                informational INTEGER NOT NULL
            );
            CREATE TABLE fls_sections (
                section_link TEXT PRIMARY KEY,
                section_id TEXT NOT NULL,
                document_link TEXT NOT NULL,
                title TEXT NOT NULL,
                number TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                informational INTEGER NOT NULL
            );
            CREATE TABLE paragraphs (
                paragraph_id TEXT PRIMARY KEY,
                paragraph_link TEXT NOT NULL,
                section_link TEXT NOT NULL,
                document_link TEXT NOT NULL,
                retrieval_eligible INTEGER NOT NULL
            );
            CREATE TABLE fls_paragraph_defined_terms (
                paragraph_id TEXT NOT NULL,
                term_text TEXT NOT NULL,
                term_target TEXT NOT NULL DEFAULT '',
                term_order INTEGER NOT NULL
            );
            CREATE TABLE fls_paragraph_term_refs (
                paragraph_id TEXT NOT NULL,
                term_text TEXT NOT NULL,
                term_target TEXT NOT NULL DEFAULT '',
                term_order INTEGER NOT NULL
            );
            CREATE TABLE fls_paragraph_syntax_defs (
                paragraph_id TEXT NOT NULL,
                symbol_text TEXT NOT NULL,
                symbol_target TEXT NOT NULL DEFAULT '',
                symbol_order INTEGER NOT NULL
            );
            CREATE TABLE fls_paragraph_syntax_refs (
                paragraph_id TEXT NOT NULL,
                symbol_text TEXT NOT NULL,
                symbol_target TEXT NOT NULL DEFAULT '',
                symbol_order INTEGER NOT NULL
            );
            CREATE TABLE fls_paragraph_std_refs (
                paragraph_id TEXT NOT NULL,
                symbol_text TEXT NOT NULL,
                symbol_target TEXT NOT NULL DEFAULT '',
                symbol_order INTEGER NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO fls_documents(document_link, title, ordinal, informational) VALUES(?, ?, ?, ?)",
            [
                ("unsafety.html", "Unsafety", 1, 0),
                ("concurrency.html", "Concurrency", 2, 0),
            ],
        )
        connection.executemany(
            "INSERT INTO fls_sections(section_link, section_id, document_link, title, number, ordinal, informational) VALUES(?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "unsafety.html#raw-pointers",
                    "raw-pointers",
                    "unsafety.html",
                    "Raw Pointers",
                    "19",
                    1,
                    0,
                ),
                ("concurrency.html#atomics", "atomics", "concurrency.html", "Atomics", "17", 2, 0),
            ],
        )
        connection.executemany(
            "INSERT INTO paragraphs(paragraph_id, paragraph_link, section_link, document_link, retrieval_eligible) VALUES(?, ?, ?, ?, ?)",
            [
                (
                    "fls_unsafe003",
                    "unsafety.html#fls_unsafe003",
                    "unsafety.html#raw-pointers",
                    "unsafety.html",
                    1,
                ),
                (
                    "fls_atomic002",
                    "concurrency.html#fls_atomic002",
                    "concurrency.html#atomics",
                    "concurrency.html",
                    1,
                ),
            ],
        )
        connection.execute(
            "INSERT INTO fls_paragraph_defined_terms(paragraph_id, term_text, term_target, term_order) VALUES(?, ?, ?, ?)",
            ("fls_unsafe003", "unsafe", "", 1),
        )
        connection.execute(
            "INSERT INTO fls_paragraph_term_refs(paragraph_id, term_text, term_target, term_order) VALUES(?, ?, ?, ?)",
            ("fls_atomic002", "atomic", "", 1),
        )
        connection.commit()


def test_build_resolution_packet_returns_grounding_artifact_only(tmp_path: Path) -> None:
    db_path = tmp_path / "fls_spec.db"
    _seed_prior_db(db_path)
    row = {
        "draft": {
            "target_id": "RET-ISSUE-001",
            "title": "Encode error-path invariants in checked APIs",
            "construct_terms": ["unsafe", "result"],
            "claim_to_evidence_map": [
                {"claim_text": "Unsafe fallback may violate pointer invariants."}
            ],
        },
        "amplification": {
            "guideline_amplification_text": "Recovery paths must preserve invariants."
        },
        "rationale": {"rationale_text": "Weak checks can expose UB in safe-callable paths."},
        "examples": {
            "non_compliant_narrative": "Continues after parse error and uses unchecked indexing.",
            "non_compliant_code": "unsafe { *values.get_unchecked(idx) }",
            "compliant_narrative": "Returns explicit errors before unsafe operations.",
            "compliant_code": 'values.get(idx).copied().ok_or("index")',
        },
        "metadata": {
            "tags": ["unsafe", "error-handling"],
            "fls_candidate": {"statement": "Weak defensive handling can lead to unsafe UB paths"},
            "editorial_metadata": {"proposed_title": "Do not use me"},
        },
    }

    packet = build_resolution_packet(row, db_path=db_path)

    assert set(packet) == {
        "governing_obligation",
        "construct_terms",
        "code_tokens",
        "supporting_phrases",
        "prior_documents",
        "prior_sections",
        "ambiguity_notes",
    }
    assert packet["governing_obligation"] == "Recovery paths must preserve invariants."
    assert packet["construct_terms"] == ["unsafe", "result"]
    assert "get_unchecked" in packet["code_tokens"]
    assert "Encode error-path invariants in checked APIs" in packet["supporting_phrases"]
    assert packet["prior_documents"]
    assert packet["prior_sections"]
    assert len(packet["prior_documents"]) <= 3
    assert len(packet["prior_sections"]) <= 5
    assert set(packet["prior_documents"][0]) == {"document_link", "score", "evidence"}
    assert set(packet["prior_sections"][0]) == {"section_link", "score", "evidence"}
    assert set(packet["prior_documents"][0]["evidence"]) == {
        "document_title_hits",
        "section_title_hits",
        "role_feature_hits",
    }
    assert "expected_domains" not in packet
    assert "field_terms" not in packet
    assert "code_symbols" not in packet
    assert all(
        value != "structured_role_match"
        for row in packet["prior_documents"] + packet["prior_sections"]
        for value in row["evidence"]["role_feature_hits"]
    )


def test_build_grounding_artifact_uses_title_only_and_ignores_narratives(tmp_path: Path) -> None:
    db_path = tmp_path / "fls_spec.db"
    _seed_prior_db(db_path)
    row = {
        "draft": {
            "title": "Unsafe pointer invariants",
            "construct_terms": [],
            "claim_to_evidence_map": [{"claim_text": "Pointer invariants must hold."}],
        },
        "amplification": {"guideline_amplification_text": ""},
        "rationale": {"rationale_text": "Pointer misuse can expose UB."},
        "examples": {
            "non_compliant_narrative": "Narrative unsafe pointer indexing text should be ignored.",
            "non_compliant_code": "unsafe { *ptr }",
            "compliant_narrative": "Narrative fallback should also be ignored.",
            "compliant_code": "ptr.read()",
        },
        "metadata": {
            "editorial_metadata": {"proposed_title": "Editorial title must not override"},
            "fls_candidate": {"statement": "Metadata statement must not override"},
            "tags": ["unsafe"],
        },
    }

    grounding = build_grounding_artifact(row, db_path=db_path)

    assert grounding["governing_obligation"] == "Pointer invariants must hold."
    assert grounding["construct_terms"][:3] == ["unsafe", "pointer", "invariants"]
    assert all("narrative" not in phrase.lower() for phrase in grounding["supporting_phrases"])
    assert "missing_draft_title" not in grounding["ambiguity_notes"]


def test_build_grounding_artifact_cleans_supporting_phrase_formatting(tmp_path: Path) -> None:
    db_path = tmp_path / "fls_spec.db"
    _seed_prior_db(db_path)
    row = {
        "draft": {
            "title": "Unsafe macro handling",
            "construct_terms": [],
            "claim_to_evidence_map": [
                {"claim_text": "| **Debugging Complexity** - Errors point elsewhere."}
            ],
        },
        "amplification": {
            "guideline_amplification_text": "- Preserve explicit unsafe review points."
        },
        "rationale": {"rationale_text": "``Unsafe`` code needs careful review."},
        "examples": {"non_compliant_code": "unsafe { macro_call!() }", "compliant_code": ""},
        "metadata": {},
    }

    grounding = build_grounding_artifact(row, db_path=db_path)

    assert all(not phrase.startswith("|") for phrase in grounding["supporting_phrases"])
    assert all("**" not in phrase for phrase in grounding["supporting_phrases"])
    assert all("``" not in phrase for phrase in grounding["supporting_phrases"])


def test_build_grounding_artifact_records_missing_title_ambiguity(tmp_path: Path) -> None:
    db_path = tmp_path / "fls_spec.db"
    _seed_prior_db(db_path)
    row = {
        "draft": {"title": "", "construct_terms": [], "claim_to_evidence_map": []},
        "amplification": {"guideline_amplification_text": ""},
        "rationale": {"rationale_text": ""},
        "examples": {"non_compliant_code": "", "compliant_code": ""},
        "metadata": {"editorial_metadata": {"proposed_title": "Fallback title should not apply"}},
    }

    grounding = build_grounding_artifact(row, db_path=db_path)

    assert "missing_draft_title" in grounding["ambiguity_notes"]
    assert grounding["governing_obligation"] == ""


def test_build_grounding_artifact_ignores_paragraph_and_chunk_free_text_for_priors(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "fls_spec.db"
    _seed_prior_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            ALTER TABLE paragraphs ADD COLUMN text TEXT NOT NULL DEFAULT '';
            CREATE TABLE chunks (
                chunk_uid TEXT PRIMARY KEY,
                section_id TEXT NOT NULL,
                clean_text TEXT NOT NULL,
                source_fetched_at TEXT NOT NULL DEFAULT ''
            );
            """
        )
        connection.execute(
            "UPDATE paragraphs SET text = 'volatile pointer aliasing escape hatch text' WHERE paragraph_id = 'fls_atomic002'"
        )
        connection.execute(
            "INSERT INTO chunks(chunk_uid, section_id, clean_text, source_fetched_at) VALUES(?, ?, ?, ?)",
            (
                "fls_atomic002",
                "atomics",
                "volatile pointer aliasing escape hatch text",
                "2026-03-08T00:00:00Z",
            ),
        )
        connection.commit()

    row = {
        "draft": {
            "title": "Volatile pointer aliasing escape hatch",
            "construct_terms": [],
            "claim_to_evidence_map": [],
        },
        "amplification": {"guideline_amplification_text": ""},
        "rationale": {"rationale_text": ""},
        "examples": {"non_compliant_code": "", "compliant_code": ""},
        "metadata": {},
    }

    grounding = build_grounding_artifact(row, db_path=db_path)

    assert grounding["prior_documents"] == []
    assert grounding["prior_sections"] == []
    assert "broad_document_priors" in grounding["ambiguity_notes"]
    assert "broad_section_priors" in grounding["ambiguity_notes"]


def test_build_grounding_artifact_is_reproducible_for_same_input_and_db(tmp_path: Path) -> None:
    db_path = tmp_path / "fls_spec.db"
    _seed_prior_db(db_path)
    row = {
        "draft": {
            "title": "Unsafe pointer invariants",
            "construct_terms": ["unsafe", "pointer"],
            "claim_to_evidence_map": [{"claim_text": "Pointer invariants must hold."}],
        },
        "amplification": {"guideline_amplification_text": "Preserve pointer validity."},
        "rationale": {"rationale_text": "Validity invariants prevent UB."},
        "examples": {
            "non_compliant_code": "unsafe { *ptr }",
            "compliant_code": "ptr.read()",
        },
        "metadata": {"tags": ["unsafe"]},
    }

    first = build_grounding_artifact(row, db_path=db_path)
    second = build_grounding_artifact(row, db_path=db_path)

    assert first == second


def test_build_grounding_artifact_ignores_identity_and_batch_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "fls_spec.db"
    _seed_prior_db(db_path)
    base = {
        "draft": {
            "target_id": "RET-ISSUE-001",
            "draft_id": "draft-alpha",
            "atom_id": "atom-alpha",
            "title": "Unsafe pointer invariants",
            "construct_terms": ["unsafe", "pointer"],
            "claim_to_evidence_map": [{"claim_text": "Pointer invariants must hold."}],
        },
        "amplification": {"guideline_amplification_text": "Preserve pointer validity."},
        "rationale": {"rationale_text": "Validity invariants prevent UB."},
        "examples": {
            "non_compliant_code": "unsafe { *ptr }",
            "compliant_code": "ptr.read()",
        },
        "metadata": {"batch_name": "Batch C", "reviewer_family": "unsafety_boundary"},
    }
    altered = {
        **base,
        "draft": {
            **base["draft"],
            "target_id": "RET-ISSUE-999",
            "draft_id": "draft-omega",
            "atom_id": "atom-omega",
        },
        "metadata": {"batch_name": "Batch Z", "reviewer_family": "architecture_types"},
    }

    first = build_grounding_artifact(base, db_path=db_path)
    second = build_grounding_artifact(altered, db_path=db_path)

    assert first == second


def test_build_grounding_artifact_can_surface_glossary_prior_evidence(tmp_path: Path) -> None:
    db_path = tmp_path / "fls_spec.db"
    _seed_prior_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO fls_documents(document_link, title, ordinal, informational) VALUES(?, ?, ?, ?)",
            ("glossary.html", "Glossary", 3, 0),
        )
        connection.execute(
            "INSERT INTO fls_sections(section_link, section_id, document_link, title, number, ordinal, informational) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                "glossary.html#unsafe-operation",
                "unsafe-operation",
                "glossary.html",
                "Unsafe Operation",
                "G1",
                3,
                0,
            ),
        )
        connection.execute(
            "INSERT INTO paragraphs(paragraph_id, paragraph_link, section_link, document_link, retrieval_eligible) VALUES(?, ?, ?, ?, ?)",
            (
                "fls_glossary001",
                "glossary.html#unsafe-operation",
                "glossary.html#unsafe-operation",
                "glossary.html",
                1,
            ),
        )
        connection.execute(
            "INSERT INTO fls_paragraph_defined_terms(paragraph_id, term_text, term_target, term_order) VALUES(?, ?, ?, ?)",
            ("fls_glossary001", "unsafe", "glossary.html#unsafe-operation", 1),
        )
        connection.commit()

    row = {
        "draft": {
            "title": "Unsafe pointer invariants",
            "construct_terms": ["unsafe", "pointer"],
            "claim_to_evidence_map": [{"claim_text": "Pointer invariants must hold."}],
        },
        "amplification": {"guideline_amplification_text": "Preserve pointer validity."},
        "rationale": {"rationale_text": "Validity invariants prevent UB."},
        "examples": {
            "non_compliant_code": "unsafe { *ptr }",
            "compliant_code": "ptr.read()",
        },
        "metadata": {},
    }

    grounding = build_grounding_artifact(row, db_path=db_path)

    assert any(
        str(item.get("section_link", "")).startswith("glossary.html#")
        for item in grounding["prior_sections"]
    )


def test_build_grounding_artifact_limits_prior_lists_and_prefers_stronger_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "fls_spec.db"
    _seed_prior_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            "INSERT INTO fls_documents(document_link, title, ordinal, informational) VALUES(?, ?, ?, ?)",
            [
                ("ffi.html", "FFI", 3, 0),
                ("functions.html", "Functions", 4, 0),
                ("items.html", "Items", 5, 0),
            ],
        )
        connection.executemany(
            "INSERT INTO fls_sections(section_link, section_id, document_link, title, number, ordinal, informational) VALUES(?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "ffi.html#external-blocks",
                    "external-blocks",
                    "ffi.html",
                    "External Blocks",
                    "20",
                    3,
                    0,
                ),
                (
                    "ffi.html#external-functions",
                    "external-functions",
                    "ffi.html",
                    "External Functions",
                    "21",
                    4,
                    0,
                ),
                (
                    "functions.html#functions",
                    "functions",
                    "functions.html",
                    "Functions",
                    "22",
                    5,
                    0,
                ),
                ("items.html#items", "items", "items.html", "Items", "23", 6, 0),
            ],
        )
        connection.executemany(
            "INSERT INTO paragraphs(paragraph_id, paragraph_link, section_link, document_link, retrieval_eligible) VALUES(?, ?, ?, ?, ?)",
            [
                ("fls_ffi001", "ffi.html#fls_ffi001", "ffi.html#external-blocks", "ffi.html", 1),
                (
                    "fls_fn001",
                    "functions.html#fls_fn001",
                    "functions.html#functions",
                    "functions.html",
                    1,
                ),
                ("fls_items001", "items.html#fls_items001", "items.html#items", "items.html", 1),
            ],
        )
        connection.executemany(
            "INSERT INTO fls_paragraph_term_refs(paragraph_id, term_text, term_target, term_order) VALUES(?, ?, ?, ?)",
            [
                ("fls_ffi001", "extern", "ffi.html#external-blocks", 1),
                ("fls_ffi001", "unsafe", "ffi.html#external-blocks", 2),
                ("fls_fn001", "function", "functions.html#functions", 1),
                ("fls_items001", "item", "items.html#items", 1),
            ],
        )
        connection.commit()

    row = {
        "draft": {
            "title": "Unsafe extern blocks",
            "construct_terms": ["unsafe", "extern"],
            "claim_to_evidence_map": [{"claim_text": "External blocks require unsafe markers."}],
        },
        "amplification": {"guideline_amplification_text": "Keep extern declarations explicit."},
        "rationale": {"rationale_text": "Extern boundaries need explicit unsafety."},
        "examples": {
            "non_compliant_code": 'extern "C" { fn malloc(); }',
            "compliant_code": 'unsafe extern "C" { fn malloc(); }',
        },
        "metadata": {},
    }

    grounding = build_grounding_artifact(row, db_path=db_path)

    assert len(grounding["prior_documents"]) <= 3
    assert len(grounding["prior_sections"]) <= 5
    assert grounding["prior_documents"][0]["document_link"] == "ffi.html"
    assert grounding["prior_sections"][0]["section_link"] == "ffi.html#external-blocks"
