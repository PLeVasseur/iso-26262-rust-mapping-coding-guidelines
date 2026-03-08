from __future__ import annotations

import json
import sqlite3
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOPOLOGY_CACHE_PATH = (
    PROJECT_ROOT / ".cache" / "fls_source" / "current" / "paragraph-ids.json"
)
DEFAULT_TOPOLOGY_URL = "https://rust-lang.github.io/fls/paragraph-ids.json"
DEFAULT_DRIFT_REPORT_PATH = (
    PROJECT_ROOT / ".cache" / "sqlite_kb" / "reports" / "fls_spec" / "topology_drift_warning.json"
)


@dataclass(frozen=True)
class TopologyDocument:
    title: str
    document_link: str
    informational: bool
    ordinal: int


@dataclass(frozen=True)
class TopologySection:
    section_id: str
    section_link: str
    section_page_link: str
    number: str
    title: str
    document_link: str
    informational: bool
    ordinal: int


@dataclass(frozen=True)
class TopologyParagraph:
    paragraph_id: str
    number: str
    paragraph_link: str
    checksum: str
    document_link: str
    section_id: str
    section_link: str
    informational: bool
    document_ordinal: int
    section_ordinal: int
    paragraph_ordinal: int


def _request_json(url: str, *, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "opencode-fls-topology"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def load_topology_payload(
    *,
    topology_path: Path | None = None,
    refresh: bool = False,
    url: str = DEFAULT_TOPOLOGY_URL,
) -> dict[str, Any]:
    resolved_path = topology_path or DEFAULT_TOPOLOGY_CACHE_PATH
    if not refresh and resolved_path.exists():
        return json.loads(resolved_path.read_text(encoding="utf-8"))
    payload = _request_json(url)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _section_link(*, page_link: str, section_id: str) -> str:
    clean_page = str(page_link or "").strip()
    clean_section = str(section_id or "").strip()
    if clean_page and "#" in clean_page:
        return clean_page
    if clean_page and clean_section:
        return f"{clean_page}#{clean_section}"
    return clean_page or clean_section


def build_topology_index(payload: dict[str, Any]) -> dict[str, Any]:
    documents_by_link: dict[str, TopologyDocument] = {}
    sections_by_link: dict[str, TopologySection] = {}
    paragraphs_by_id: dict[str, TopologyParagraph] = {}
    paragraph_ids_by_document: dict[str, list[str]] = {}
    paragraph_ids_by_section: dict[str, list[str]] = {}

    raw_documents = payload.get("documents") if isinstance(payload, dict) else []
    if not isinstance(raw_documents, list):
        raw_documents = []
    for document_ordinal, raw_document in enumerate(raw_documents):
        if not isinstance(raw_document, dict):
            continue
        document_link = str(raw_document.get("link") or "").strip()
        if not document_link:
            continue
        document = TopologyDocument(
            title=str(raw_document.get("title") or "").strip(),
            document_link=document_link,
            informational=bool(raw_document.get("informational", False)),
            ordinal=document_ordinal,
        )
        documents_by_link[document_link] = document
        paragraph_ids_by_document.setdefault(document_link, [])

        raw_sections = raw_document.get("sections") if isinstance(raw_document, dict) else []
        if not isinstance(raw_sections, list):
            raw_sections = []
        for section_ordinal, raw_section in enumerate(raw_sections):
            if not isinstance(raw_section, dict):
                continue
            section_id = str(raw_section.get("id") or "").strip()
            page_link = str(raw_section.get("link") or document_link).strip()
            section_link = _section_link(page_link=page_link, section_id=section_id)
            if not section_link:
                continue
            section = TopologySection(
                section_id=section_id,
                section_link=section_link,
                section_page_link=page_link,
                number=str(raw_section.get("number") or "").strip(),
                title=str(raw_section.get("title") or "").strip(),
                document_link=document_link,
                informational=bool(raw_section.get("informational", False)),
                ordinal=section_ordinal,
            )
            sections_by_link[section_link] = section
            paragraph_ids_by_section.setdefault(section_link, [])

            raw_paragraphs = raw_section.get("paragraphs") if isinstance(raw_section, dict) else []
            if not isinstance(raw_paragraphs, list):
                raw_paragraphs = []
            for paragraph_ordinal, raw_paragraph in enumerate(raw_paragraphs):
                if not isinstance(raw_paragraph, dict):
                    continue
                paragraph_id = str(raw_paragraph.get("id") or "").strip()
                if not paragraph_id:
                    continue
                paragraph = TopologyParagraph(
                    paragraph_id=paragraph_id,
                    number=str(raw_paragraph.get("number") or "").strip(),
                    paragraph_link=str(raw_paragraph.get("link") or "").strip(),
                    checksum=str(raw_paragraph.get("checksum") or "").strip(),
                    document_link=document_link,
                    section_id=section_id,
                    section_link=section_link,
                    informational=bool(raw_section.get("informational", False))
                    or bool(raw_document.get("informational", False)),
                    document_ordinal=document_ordinal,
                    section_ordinal=section_ordinal,
                    paragraph_ordinal=paragraph_ordinal,
                )
                paragraphs_by_id[paragraph_id] = paragraph
                paragraph_ids_by_document[document_link].append(paragraph_id)
                paragraph_ids_by_section[section_link].append(paragraph_id)

    return {
        "documents_by_link": documents_by_link,
        "sections_by_link": sections_by_link,
        "paragraphs_by_id": paragraphs_by_id,
        "paragraph_ids_by_document": paragraph_ids_by_document,
        "paragraph_ids_by_section": paragraph_ids_by_section,
    }


def load_topology_index(
    *,
    topology_path: Path | None = None,
    refresh: bool = False,
    url: str = DEFAULT_TOPOLOGY_URL,
) -> dict[str, Any]:
    payload = load_topology_payload(topology_path=topology_path, refresh=refresh, url=url)
    return build_topology_index(payload)


def get_document(index: dict[str, Any], document_link: str) -> TopologyDocument | None:
    return index.get("documents_by_link", {}).get(str(document_link).strip())


def get_section(index: dict[str, Any], section_link: str) -> TopologySection | None:
    return index.get("sections_by_link", {}).get(str(section_link).strip())


def get_paragraph(index: dict[str, Any], paragraph_id: str) -> TopologyParagraph | None:
    return index.get("paragraphs_by_id", {}).get(str(paragraph_id).strip())


def paragraph_ids_for_section(index: dict[str, Any], section_link: str) -> list[str]:
    return list(index.get("paragraph_ids_by_section", {}).get(str(section_link).strip(), []))


def paragraph_ids_for_document(index: dict[str, Any], document_link: str) -> list[str]:
    return list(index.get("paragraph_ids_by_document", {}).get(str(document_link).strip(), []))


def topology_drift_report(
    *,
    db_path: Path,
    topology_index: dict[str, Any],
    report_path: Path = DEFAULT_DRIFT_REPORT_PATH,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "db_path": str(db_path),
        "warning": False,
        "paragraphs_missing_in_live_topology": [],
        "mismatched_fields": [],
    }
    if not db_path.exists():
        return report

    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute(
            """
            SELECT
                p.paragraph_id,
                p.paragraph_number,
                p.document_link,
                p.paragraph_link,
                p.section_link,
                p.checksum,
                COALESCE(d.informational, 0) AS document_informational,
                COALESCE(s.informational, 0) AS section_informational
            FROM paragraphs AS p
            LEFT JOIN fls_documents AS d ON d.document_link = p.document_link
            LEFT JOIN fls_sections AS s ON s.section_link = p.section_link
            ORDER BY p.paragraph_id ASC
            """
        ).fetchall()
    finally:
        connection.close()

    for row in rows:
        paragraph_id = str(row[0] or "")
        live = get_paragraph(topology_index, paragraph_id)
        if live is None:
            report["warning"] = True
            report["paragraphs_missing_in_live_topology"].append(paragraph_id)
            continue
        live_document = get_document(topology_index, live.document_link)
        live_section = get_section(topology_index, live.section_link)
        comparisons = {
            "paragraph_number": (str(row[1] or ""), live.number),
            "document_link": (str(row[2] or ""), live.document_link),
            "paragraph_link": (str(row[3] or ""), live.paragraph_link),
            "section_link": (str(row[4] or ""), live.section_link),
            "checksum": (str(row[5] or ""), live.checksum),
            "document_informational": (
                int(row[6] or 0),
                int(live_document.informational)
                if live_document is not None
                else int(live.informational),
            ),
            "section_informational": (
                int(row[7] or 0),
                int(live_section.informational)
                if live_section is not None
                else int(live.informational),
            ),
        }
        for field_name, (db_value, live_value) in comparisons.items():
            if db_value != live_value:
                report["warning"] = True
                report["mismatched_fields"].append(
                    {
                        "paragraph_id": paragraph_id,
                        "field": field_name,
                        "db_value": db_value,
                        "live_value": live_value,
                    }
                )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
