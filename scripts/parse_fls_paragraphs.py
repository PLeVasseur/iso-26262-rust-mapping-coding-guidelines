"""Parse FLS RST sources into paragraph records."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from context.fls_topology import build_topology_index, load_topology_payload

HEADING_CHARS = {"=": 0, "-": 1, "~": 2, "^": 3}
DEFAULT_GUIDELINES_REPO = Path(
    os.environ.get(
        "GUIDELINES_REPO", "/Users/pete.levasseur/personal/safety-critical-rust-coding-guidelines"
    )
)
DEFAULT_SPEC_LOCK_PATH = DEFAULT_GUIDELINES_REPO / "src" / "spec.lock"
DEFAULT_TOPOLOGY_PATH = Path(".cache/fls_source/current/paragraph-ids.json")

ROLE_PATTERN = re.compile(r":(?P<role>dp|p|dt|t|ds|s|std):`(?P<content>[^`]*)`")
DISPLAY_TARGET_PATTERN = re.compile(r"^(?P<display>.+?)\s*<(?P<target>[^>]+)>$")


@dataclass(frozen=True)
class FLSParagraph:
    paragraph_id: str
    paragraph_number: str
    chapter: str
    section: str
    subsection: str
    raw_text: str
    clean_text: str
    source_file: str
    document_link: str
    paragraph_link: str
    section_link: str
    section_id: str
    checksum: str
    defined_terms: tuple[str, ...]
    term_refs: tuple[str, ...]
    syntax_defs: tuple[str, ...]
    syntax_refs: tuple[str, ...]
    std_refs: tuple[str, ...]
    paragraph_refs: tuple[str, ...]
    defined_term_targets: tuple[str, ...]
    term_ref_targets: tuple[str, ...]
    syntax_def_targets: tuple[str, ...]
    syntax_ref_targets: tuple[str, ...]
    std_ref_targets: tuple[str, ...]
    paragraph_ref_targets: tuple[str, ...]


def load_paragraph_numbers(spec_lock_path: Path = DEFAULT_SPEC_LOCK_PATH) -> dict[str, str]:
    if not spec_lock_path.exists():
        return {}

    try:
        lock_data = json.loads(spec_lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    number_by_id: dict[str, str] = {}
    for document in lock_data.get("documents", []):
        for section in document.get("sections", []):
            section_id = str(section.get("id") or "")
            section_number = str(section.get("number") or "")
            if section_id and section_number:
                number_by_id[section_id] = section_number
            for paragraph in section.get("paragraphs", []):
                paragraph_id = str(paragraph.get("id") or "")
                paragraph_number = str(paragraph.get("number") or "")
                if paragraph_id and paragraph_number:
                    number_by_id[paragraph_id] = paragraph_number
    return number_by_id


def _load_paragraph_metadata(
    *, topology_path: Path | None, spec_lock_path: Path
) -> dict[str, dict[str, str]]:
    metadata: dict[str, dict[str, str]] = {}
    if topology_path is not None and topology_path.exists():
        payload = load_topology_payload(topology_path=topology_path, refresh=False)
        topology = build_topology_index(payload)
        for paragraph_id, paragraph in topology["paragraphs_by_id"].items():
            metadata[str(paragraph_id)] = {
                "paragraph_number": str(paragraph.number),
                "document_link": str(paragraph.document_link),
                "paragraph_link": str(paragraph.paragraph_link),
                "section_link": str(paragraph.section_link),
                "section_id": str(paragraph.section_id),
                "checksum": str(paragraph.checksum),
            }
    paragraph_numbers = load_paragraph_numbers(spec_lock_path=spec_lock_path)
    for paragraph_id, number in paragraph_numbers.items():
        current = metadata.setdefault(str(paragraph_id), {})
        current.setdefault("paragraph_number", str(number))
    return metadata


def _is_heading_underline(line: str) -> int | None:
    stripped = line.strip()
    if len(stripped) < 3:
        return None
    first = stripped[0]
    if first in HEADING_CHARS and stripped == first * len(stripped):
        return HEADING_CHARS[first]
    return None


def _normalize_role_text(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\[([^\]]+)\]", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _split_role_content(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    match = DISPLAY_TARGET_PATTERN.match(text)
    if match:
        display = _normalize_role_text(match.group("display"))
        target = _normalize_role_text(match.group("target"))
        return display, target
    normalized = _normalize_role_text(text)
    return normalized, ""


def _strip_rst_markup(text: str) -> str:
    text = re.sub(r":[a-z_]+:`([^`]*)`", r"\1", text)
    text = re.sub(r"``([^`]*)``", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_dp_id(line: str) -> tuple[str, str]:
    match = re.search(r":dp:`(fls_[A-Za-z0-9_]+)`", line)
    if not match:
        return "", ""
    paragraph_id = match.group(1)
    after = line[match.end() :].strip()
    return paragraph_id, after


def _role_payloads(raw_text: str) -> dict[str, tuple[str, ...]]:
    buckets: dict[str, list[str]] = {
        "dt": [],
        "t": [],
        "ds": [],
        "s": [],
        "std": [],
        "p": [],
    }
    targets: dict[str, list[str]] = {
        "dt": [],
        "t": [],
        "ds": [],
        "s": [],
        "std": [],
        "p": [],
    }
    for match in ROLE_PATTERN.finditer(raw_text):
        role = str(match.group("role") or "").strip()
        if role == "dp":
            continue
        normalized, target = _split_role_content(match.group("content"))
        if not normalized and not target:
            continue
        bucket = buckets.get(role)
        if bucket is not None:
            bucket.append(normalized)
            targets[role].append(target)
    return {
        "defined_terms": tuple(buckets["dt"]),
        "term_refs": tuple(buckets["t"]),
        "syntax_defs": tuple(buckets["ds"]),
        "syntax_refs": tuple(buckets["s"]),
        "std_refs": tuple(buckets["std"]),
        "paragraph_refs": tuple(buckets["p"]),
        "defined_term_targets": tuple(targets["dt"]),
        "term_ref_targets": tuple(targets["t"]),
        "syntax_def_targets": tuple(targets["ds"]),
        "syntax_ref_targets": tuple(targets["s"]),
        "std_ref_targets": tuple(targets["std"]),
        "paragraph_ref_targets": tuple(targets["p"]),
    }


def parse_fls_rst(
    rst_path: Path,
    *,
    paragraph_numbers: dict[str, str] | None = None,
    paragraph_metadata: dict[str, dict[str, str]] | None = None,
) -> list[FLSParagraph]:
    content = rst_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    source_file = rst_path.name

    paragraphs: list[FLSParagraph] = []
    headings = ["", "", "", ""]

    index = 0
    while index < len(lines):
        line = lines[index]

        if index + 1 < len(lines):
            level = _is_heading_underline(lines[index + 1])
            if level is not None and line.strip():
                headings[level] = line.strip()
                for deeper in range(level + 1, len(headings)):
                    headings[deeper] = ""
                index += 2
                continue

        paragraph_id, inline_text = _extract_dp_id(line)
        if not paragraph_id:
            index += 1
            continue

        metadata = dict((paragraph_metadata or {}).get(paragraph_id, {}))
        paragraph_number = ""
        if paragraph_numbers is not None and paragraph_numbers:
            paragraph_number = paragraph_numbers.get(paragraph_id, "")
        paragraph_number = str(metadata.get("paragraph_number") or paragraph_number or "")

        paragraph_lines: list[str] = []
        if inline_text:
            paragraph_lines.append(inline_text)

        cursor = index + 1
        while cursor < len(lines):
            raw = lines[cursor]
            stripped = raw.strip()

            if _extract_dp_id(raw)[0]:
                break
            if (
                cursor + 1 < len(lines)
                and stripped
                and _is_heading_underline(lines[cursor + 1]) is not None
            ):
                break
            if re.match(r"^\.\.\s+_fls_[A-Za-z0-9_]+:\s*$", stripped):
                break

            if not stripped:
                cursor += 1
                continue

            if stripped.startswith(".. ") and not stripped.startswith(".. _fls_"):
                break

            paragraph_lines.append(stripped)
            cursor += 1

        raw_paragraph_text = " ".join(paragraph_lines)
        paragraph_text = _strip_rst_markup(raw_paragraph_text)
        role_payloads = _role_payloads(raw_paragraph_text)
        if paragraph_text:
            default_document_link = f"{rst_path.stem}.html"
            default_paragraph_link = f"{default_document_link}#{paragraph_id}"
            default_section_link = default_document_link
            paragraphs.append(
                FLSParagraph(
                    paragraph_id=paragraph_id,
                    paragraph_number=paragraph_number,
                    chapter=headings[0],
                    section=headings[1],
                    subsection=headings[2],
                    raw_text=raw_paragraph_text,
                    clean_text=paragraph_text,
                    source_file=source_file,
                    document_link=str(metadata.get("document_link") or default_document_link),
                    paragraph_link=str(metadata.get("paragraph_link") or default_paragraph_link),
                    section_link=str(metadata.get("section_link") or default_section_link),
                    section_id=str(metadata.get("section_id") or ""),
                    checksum=str(metadata.get("checksum") or ""),
                    defined_terms=role_payloads["defined_terms"],
                    term_refs=role_payloads["term_refs"],
                    syntax_defs=role_payloads["syntax_defs"],
                    syntax_refs=role_payloads["syntax_refs"],
                    std_refs=role_payloads["std_refs"],
                    paragraph_refs=role_payloads["paragraph_refs"],
                    defined_term_targets=role_payloads["defined_term_targets"],
                    term_ref_targets=role_payloads["term_ref_targets"],
                    syntax_def_targets=role_payloads["syntax_def_targets"],
                    syntax_ref_targets=role_payloads["syntax_ref_targets"],
                    std_ref_targets=role_payloads["std_ref_targets"],
                    paragraph_ref_targets=role_payloads["paragraph_ref_targets"],
                )
            )

        index = cursor

    return paragraphs


def parse_all_fls(
    source_dir: Path,
    *,
    paragraph_numbers: dict[str, str] | None = None,
    spec_lock_path: Path = DEFAULT_SPEC_LOCK_PATH,
    topology_path: Path | None = DEFAULT_TOPOLOGY_PATH,
) -> list[FLSParagraph]:
    if not source_dir.exists():
        raise FileNotFoundError(f"FLS source directory not found: {source_dir}")

    if paragraph_numbers is None:
        paragraph_numbers = load_paragraph_numbers(spec_lock_path=spec_lock_path)
    paragraph_metadata = _load_paragraph_metadata(
        topology_path=topology_path,
        spec_lock_path=spec_lock_path,
    )
    all_paragraphs: list[FLSParagraph] = []
    for rst_file in sorted(source_dir.glob("*.rst")):
        if rst_file.name.startswith("_"):
            continue
        all_paragraphs.extend(
            parse_fls_rst(
                rst_file,
                paragraph_numbers=paragraph_numbers,
                paragraph_metadata=paragraph_metadata,
            )
        )
    return all_paragraphs
