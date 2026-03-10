from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from retrieval.build.chunk_fts_validation import validate_chunk_fts_mapping_db


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def validate_chunk_first_db(db_path: Path, *, corpus: str) -> dict[str, Any]:
    mapping = validate_chunk_fts_mapping_db(db_path)
    failures: list[str] = []
    warnings: list[str] = []

    connection = sqlite3.connect(db_path)
    try:
        chunk_count = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        chunks_fts_count = int(connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0])
        schema_user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        latest_migration_id = ""
        if _table_exists(connection, "schema_version"):
            row = connection.execute(
                "SELECT latest_migration_id FROM schema_version WHERE schema_id = ?",
                ("sqlite_kb",),
            ).fetchone()
            latest_migration_id = str(row[0] or "") if row else ""
        latest_snapshot_id = ""
        if _table_exists(connection, "snapshots"):
            row = connection.execute(
                "SELECT snapshot_id FROM snapshots ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            latest_snapshot_id = str(row[0] or "") if row else ""
    finally:
        connection.close()

    if chunk_count == 0:
        failures.append("No chunks materialized")
    if chunks_fts_count == 0:
        failures.append("No chunks_fts rows materialized")
    if mapping.get("applicable") and not mapping.get("passed", False):
        failures.append("chunk_fts_rowids mapping coverage is incomplete")
    if mapping.get("applicable") and int(mapping.get("chunk_count", 0)) != chunk_count:
        failures.append("chunk_fts_rowids chunk count disagrees with chunks table")
    if mapping.get("applicable") and int(mapping.get("chunks_fts_count", 0)) != chunks_fts_count:
        failures.append("chunk_fts_rowids chunk count disagrees with chunks_fts table")
    if not mapping.get("applicable"):
        warnings.append("chunk_fts_rowids mapping not applicable for this database")

    return {
        "corpus": str(corpus),
        "db_path": str(db_path.resolve()),
        "db_sha256": _sha256(db_path),
        "checked_at": utc_now(),
        "passed": not failures,
        "failures": failures,
        "warnings": warnings,
        "chunk_count": chunk_count,
        "chunks_fts_count": chunks_fts_count,
        "schema_user_version": schema_user_version,
        "latest_migration_id": latest_migration_id,
        "latest_snapshot_id": latest_snapshot_id,
        "chunk_fts_mapping": mapping,
    }


def write_chunk_first_validation_report(
    *,
    report_root: Path,
    corpus: str,
    snapshot_id: str,
    payload: dict[str, Any],
) -> Path:
    report_root.mkdir(parents=True, exist_ok=True)
    path = report_root / f"{snapshot_id}_chunk_first_validation.json"
    output = {
        "corpus": str(corpus),
        "snapshot_id": str(snapshot_id),
        **payload,
    }
    path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_current_chunk_first_validation_report(
    *,
    report_root: Path,
    corpus: str,
    payload: dict[str, Any],
) -> Path:
    report_root.mkdir(parents=True, exist_ok=True)
    path = report_root / "current_chunk_first_validation.json"
    output = {
        "corpus": str(corpus),
        "snapshot_id": str(payload.get("latest_snapshot_id", "") or "current"),
        **payload,
    }
    path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def validate_guidelines_repo_db(db_path: Path) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []

    connection = sqlite3.connect(db_path)
    try:
        guideline_count = int(
            connection.execute("SELECT COUNT(*) FROM guideline_records").fetchone()[0]
        )
        block_count = int(connection.execute("SELECT COUNT(*) FROM guideline_blocks").fetchone()[0])
        citation_count = int(
            connection.execute("SELECT COUNT(*) FROM guideline_citations").fetchone()[0]
        )
        bibliography_count = int(
            connection.execute("SELECT COUNT(*) FROM guideline_bibliography").fetchone()[0]
        )
        bib_link_count = int(
            connection.execute("SELECT COUNT(*) FROM guideline_bib_links").fetchone()[0]
        )
        exemplar_count = int(
            connection.execute("SELECT COUNT(*) FROM guideline_exemplars").fetchone()[0]
        )
        schema_user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])

        latest_migration_id = ""
        if _table_exists(connection, "schema_version"):
            row = connection.execute(
                "SELECT latest_migration_id FROM schema_version WHERE schema_id = ?",
                ("sqlite_kb",),
            ).fetchone()
            latest_migration_id = str(row[0] or "") if row else ""

        latest_snapshot_id = ""
        latest_commit_sha = ""
        if _table_exists(connection, "snapshots"):
            row = connection.execute(
                "SELECT snapshot_id, commit_sha FROM snapshots ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                latest_snapshot_id = str(row[0] or "")
                latest_commit_sha = str(row[1] or "")

        duplicate_guideline_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT guideline_id, COUNT(*) AS c
                    FROM guideline_records
                    GROUP BY guideline_id
                    HAVING c > 1
                )
                """
            ).fetchone()[0]
        )
        missing_source_file_path_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM guideline_records WHERE TRIM(source_file_path) = ''"
            ).fetchone()[0]
        )
    finally:
        connection.close()

    if guideline_count <= 0:
        failures.append("No guideline_records rows materialized")
    if block_count <= 0:
        failures.append("No guideline_blocks rows materialized")
    if block_count < guideline_count:
        failures.append("guideline_blocks coverage is lower than guideline_records")
    if not latest_snapshot_id:
        failures.append("No snapshots row recorded for guidelines_repo")
    if not latest_commit_sha:
        failures.append("Latest guidelines_repo snapshot is missing commit_sha")
    if not latest_migration_id:
        failures.append("Missing latest_migration_id for guidelines_repo")
    if duplicate_guideline_count > 0:
        failures.append("Duplicate guideline_id values detected")
    if missing_source_file_path_count > 0:
        failures.append("Guideline rows with missing source_file_path detected")
    if bibliography_count > 0 and bib_link_count == 0:
        failures.append("Bibliography rows exist without guideline_bib_links coverage")
    if citation_count == 0:
        warnings.append("No guideline_citations rows materialized")
    if exemplar_count == 0:
        warnings.append("No guideline_exemplars rows materialized")

    return {
        "corpus": "guidelines_repo",
        "db_path": str(db_path.resolve()),
        "db_sha256": _sha256(db_path),
        "checked_at": utc_now(),
        "passed": not failures,
        "failures": failures,
        "warnings": warnings,
        "schema_user_version": schema_user_version,
        "latest_migration_id": latest_migration_id,
        "latest_snapshot_id": latest_snapshot_id,
        "latest_commit_sha": latest_commit_sha,
        "table_counts": {
            "guideline_records": guideline_count,
            "guideline_blocks": block_count,
            "guideline_citations": citation_count,
            "guideline_bibliography": bibliography_count,
            "guideline_bib_links": bib_link_count,
            "guideline_exemplars": exemplar_count,
        },
    }


def write_current_guidelines_repo_validation_report(
    *,
    report_root: Path,
    payload: dict[str, Any],
) -> Path:
    report_root.mkdir(parents=True, exist_ok=True)
    path = report_root / "current_validation.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "updated_at": utc_now(), "databases": {}}
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        return {"version": 1, "updated_at": utc_now(), "databases": {}}
    payload.setdefault("version", 1)
    payload.setdefault("databases", {})
    return payload


def read_previous_snapshot_path(
    manifest_payload: dict[str, Any],
    *,
    manifest_path: Path,
) -> Path | None:
    rust_ref = (manifest_payload.get("databases") or {}).get("rust_reference") or {}
    snapshot_path = rust_ref.get("snapshot_path")
    if isinstance(snapshot_path, str) and snapshot_path:
        candidate = Path(snapshot_path)
        if candidate.is_absolute():
            return candidate
        return (manifest_path.parent / candidate).resolve()
    return None


def validate_rust_reference_db(
    db_path: Path,
    previous_snapshot_path: Path | None = None,
    min_sections: int = 20,
    min_statements: int = 50,
    min_mechanisms: int = 6,
) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    chunk_mapping = validate_chunk_fts_mapping_db(db_path)

    connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row

        section_count = int(connection.execute("SELECT COUNT(*) FROM sections").fetchone()[0])
        statement_count = int(connection.execute("SELECT COUNT(*) FROM statements").fetchone()[0])
        mechanism_count = int(connection.execute("SELECT COUNT(*) FROM mechanisms").fetchone()[0])
        semantic_model_count = int(
            connection.execute("SELECT COUNT(*) FROM semantic_models").fetchone()[0]
        )
        semantic_corpus_count = int(
            connection.execute("SELECT COUNT(*) FROM semantic_corpus").fetchone()[0]
        )
        statement_embedding_count = int(
            connection.execute("SELECT COUNT(*) FROM statement_embeddings").fetchone()[0]
        )
        statements_fts_count = int(
            connection.execute("SELECT COUNT(*) FROM statements_fts").fetchone()[0]
        )
        row_mechanism_score_count = int(
            connection.execute("SELECT COUNT(*) FROM row_mechanism_scores").fetchone()[0]
        )
        chunk_count = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        chunks_fts_count = int(connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0])

        if section_count < int(min_sections):
            failures.append("Too few sections extracted from Rust Reference")
        if statement_count < int(min_statements):
            failures.append("Too few semantic statements extracted from Rust Reference")
        if mechanism_count < int(min_mechanisms):
            failures.append("Too few mechanisms extracted from Rust Reference")
        if semantic_model_count < 2:
            failures.append("Semantic model metadata is incomplete")
        if semantic_corpus_count < int(mechanism_count + 9):
            failures.append("Semantic corpus coverage is below minimum row/mechanism threshold")
        if statements_fts_count != statement_count:
            failures.append("FTS statement index coverage does not match statements table")
        if row_mechanism_score_count < 9:
            failures.append("Row mechanism score coverage is incomplete")
        if chunk_mapping.get("applicable") and not chunk_mapping.get("passed", False):
            failures.append("chunk_fts_rowids mapping coverage is incomplete")
        elif chunk_mapping.get("applicable") and (
            int(chunk_mapping.get("chunk_count", 0)) != chunk_count
            or int(chunk_mapping.get("chunks_fts_count", 0)) != chunks_fts_count
        ):
            failures.append("chunk_fts_rowids diagnostics disagree with chunk coverage counts")
        if statement_embedding_count == 0:
            warnings.append(
                "No statement embeddings materialized yet; "
                "semantic retrieval will compute on demand"
            )

        duplicate_section_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT section_id, COUNT(*) AS c
                    FROM sections
                    GROUP BY section_id
                    HAVING c > 1
                )
                """
            ).fetchone()[0]
        )
        if duplicate_section_count > 0:
            failures.append("Duplicate section_id values detected")

        missing_anchor_count = int(
            connection.execute("SELECT COUNT(*) FROM sections WHERE TRIM(anchor) = ''").fetchone()[
                0
            ]
        )
        if missing_anchor_count > 0:
            failures.append("Sections with missing anchor detected")

        safety_terms = {
            "type": ("type",),
            "unsafe": ("unsafe",),
            "trait": ("trait",),
            "concurrency": ("concurrency", "thread", "send", "sync", "atomic"),
        }
        safety_rows = connection.execute(
            """
            SELECT title AS text_value FROM chapters
            UNION ALL
            SELECT heading AS text_value FROM sections
            """
        ).fetchall()
        safety_text = "\n".join(str(row["text_value"]).lower() for row in safety_rows)
        for required_term, aliases in safety_terms.items():
            if not any(alias in safety_text for alias in aliases):
                failures.append(f"Required high-safety chapter token missing: {required_term}")

        verdict_rows = connection.execute(
            """
            SELECT r.row_marker, rv.verdict, rv.rationale, rv.rationale_anchor
            FROM table1_rows AS r
            JOIN row_verdicts AS rv ON rv.row_node_id = r.row_node_id
            ORDER BY r.row_marker
            """
        ).fetchall()
        if len(verdict_rows) != 9:
            failures.append("Table 1 verdict coverage is incomplete")

        expected_markers = {f"1{chr(ord('a') + idx)}" for idx in range(9)}
        markers = {str(row["row_marker"]) for row in verdict_rows}
        if markers != expected_markers:
            failures.append("Table 1 marker set mismatch")

        for row in verdict_rows:
            verdict = str(row["verdict"])
            if verdict not in {"applicable", "not_applicable"}:
                failures.append(f"Invalid verdict value: {verdict}")
                continue

            if verdict == "not_applicable" and (
                not str(row["rationale"]).strip() or not str(row["rationale_anchor"]).strip()
            ):
                failures.append(
                    f"Missing rationale evidence for not_applicable row {row['row_marker']}"
                )

            if verdict == "applicable":
                count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM row_mechanisms
                        WHERE row_node_id = (
                            SELECT row_node_id
                            FROM table1_rows
                            WHERE row_marker = ?
                        )
                        """,
                        (row["row_marker"],),
                    ).fetchone()[0]
                )
                if count < 1:
                    failures.append(f"Applicable row {row['row_marker']} has no mechanisms")

        if previous_snapshot_path is not None and previous_snapshot_path.exists():
            previous_connection = sqlite3.connect(previous_snapshot_path)
            try:
                previous_section_count = int(
                    previous_connection.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
                )
                previous_statement_count = int(
                    previous_connection.execute("SELECT COUNT(*) FROM statements").fetchone()[0]
                )
            except sqlite3.Error:
                previous_section_count = 0
                previous_statement_count = 0
            finally:
                previous_connection.close()

            if previous_section_count > 0:
                section_delta_ratio = abs(section_count - previous_section_count) / float(
                    previous_section_count
                )
                if section_delta_ratio > 0.35:
                    warnings.append(
                        "Section count drift exceeds 35% compared with previous snapshot "
                        f"({previous_section_count} -> {section_count})"
                    )

            if previous_statement_count > 0:
                statement_delta_ratio = abs(statement_count - previous_statement_count) / float(
                    previous_statement_count
                )
                if statement_delta_ratio > 0.35:
                    warnings.append(
                        "Statement count drift exceeds 35% compared with previous snapshot "
                        f"({previous_statement_count} -> {statement_count})"
                    )
    finally:
        connection.close()

    return {
        "passed": not failures,
        "checked_at": utc_now(),
        "failures": failures,
        "warnings": warnings,
        "chunk_fts_mapping": chunk_mapping,
    }


def write_validation_report(report_root: Path, snapshot_id: str, payload: dict[str, Any]) -> Path:
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / f"{snapshot_id}.json"
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return report_path


def write_row_metadata_report(
    report_root: Path,
    snapshot_id: str,
    table_rows: list[dict[str, Any]],
) -> Path:
    report_root.mkdir(parents=True, exist_ok=True)
    rows = sorted(table_rows, key=lambda row: str(row.get("row_marker", "")))
    payload = {
        "snapshot_id": snapshot_id,
        "generated_at": utc_now(),
        "rows": [
            {
                "row_marker": str(row.get("row_marker", "")),
                "row_node_id": str(row.get("row_node_id", "")),
                "requirement_text_len": len(str(row.get("requirement_text", ""))),
                "profile_term_count": len(list(row.get("row_profile_terms", []))),
                "footnote_count": len(list(row.get("row_footnotes", []))),
            }
            for row in rows
        ],
    }
    report_path = report_root / f"{snapshot_id}_table1_row_metadata.json"
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return report_path


def update_manifest(
    manifest_path: Path,
    *,
    snapshot_id: str,
    current_db_path: Path,
    snapshot_db_path: Path,
    commit_sha: str,
    source_fetched_at: str,
    source_url: str,
    report_path: Path,
    row_metadata_report_path: Path,
    chunk_first_report_path: Path,
    counts: dict[str, int],
    chunk_count: int,
    chunk_overlap_percent: float,
    retrieval_mode: str,
    retrieval_corpus: str,
    semantic_profile_version: str,
    embedding_model_id: str,
    reranker_model_id: str,
    chunk_fts_mapping: dict[str, Any] | None = None,
) -> None:
    base_dir = manifest_path.resolve().parent

    def _repo_relative(path: Path) -> str:
        resolved = path.resolve()
        try:
            return str(resolved.relative_to(base_dir))
        except ValueError:
            return str(resolved)

    manifest = load_manifest(manifest_path)
    manifest.setdefault("databases", {})
    manifest["updated_at"] = utc_now()
    query_contract_path = (
        "config/sqlite_query_contracts/rust_reference_chunk.yaml"
        if retrieval_corpus == "chunk"
        else "config/sqlite_query_contracts/rust_reference.yaml"
    )
    manifest["databases"]["rust_reference"] = {
        "db_name": "rust_reference.sqlite",
        "current_path": _repo_relative(current_db_path),
        "snapshot_id": snapshot_id,
        "snapshot_path": _repo_relative(snapshot_db_path),
        "source": {
            "kind": "rust-reference",
            "ref": source_url,
            "commit_sha": commit_sha,
            "fetched_at": source_fetched_at,
        },
        "query_contract": query_contract_path,
        "validation_report": _repo_relative(report_path),
        "row_metadata_report": _repo_relative(row_metadata_report_path),
        "chunk_first_validation_report": _repo_relative(chunk_first_report_path),
        "semantic_retrieval": {
            "retrieval_mode": retrieval_mode,
            "retrieval_corpus": retrieval_corpus,
            "profile_version": semantic_profile_version,
            "embedding_model_id": embedding_model_id,
            "reranker_model_id": reranker_model_id,
        },
        "table1_queryability": {
            "rows_total": counts["applicable"] + counts["not_applicable"],
            "applicable": counts["applicable"],
            "not_applicable": counts["not_applicable"],
        },
        "chunk_stats": {
            "chunk_count": int(chunk_count),
            "chunk_overlap_percent": float(chunk_overlap_percent),
        },
    }
    if chunk_fts_mapping is not None:
        manifest["databases"]["rust_reference"]["chunk_fts_mapping"] = chunk_fts_mapping

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(manifest, handle, sort_keys=False, allow_unicode=False)
