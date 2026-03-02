"""Parse FLS RST sources into paragraph records."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

HEADING_CHARS = {"=": 0, "-": 1, "~": 2, "^": 3}
DEFAULT_GUIDELINES_REPO = Path(
    os.environ.get(
        "GUIDELINES_REPO", "/Users/pete.levasseur/personal/safety-critical-rust-coding-guidelines"
    )
)
DEFAULT_SPEC_LOCK_PATH = DEFAULT_GUIDELINES_REPO / "src" / "spec.lock"


@dataclass(frozen=True)
class FLSParagraph:
    paragraph_id: str
    paragraph_number: str
    chapter: str
    section: str
    subsection: str
    text: str
    source_file: str


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


def _is_heading_underline(line: str) -> int | None:
    stripped = line.strip()
    if len(stripped) < 3:
        return None
    first = stripped[0]
    if first in HEADING_CHARS and stripped == first * len(stripped):
        return HEADING_CHARS[first]
    return None


def _strip_rst_markup(text: str) -> str:
    text = re.sub(r":[a-z_]+:`([^`]*)`", r"\1", text)
    text = re.sub(r"``([^`]*)``", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_dp_id(line: str) -> tuple[str, str]:
    match = re.search(r":dp:`(fls_[A-Za-z0-9_]+)`", line)
    if not match:
        return "", ""
    paragraph_id = match.group(1)
    after = line[match.end() :].strip()
    return paragraph_id, after


def parse_fls_rst(
    rst_path: Path,
    *,
    paragraph_numbers: dict[str, str] | None = None,
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

        paragraph_number = ""
        if paragraph_numbers is not None:
            if paragraph_numbers and paragraph_id not in paragraph_numbers:
                index += 1
                continue
            paragraph_number = paragraph_numbers.get(paragraph_id, "")
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

        paragraph_text = _strip_rst_markup(" ".join(paragraph_lines))
        if paragraph_text:
            paragraphs.append(
                FLSParagraph(
                    paragraph_id=paragraph_id,
                    paragraph_number=paragraph_number,
                    chapter=headings[0],
                    section=headings[1],
                    subsection=headings[2],
                    text=paragraph_text,
                    source_file=source_file,
                )
            )

        index = cursor

    return paragraphs


def parse_all_fls(
    source_dir: Path,
    *,
    paragraph_numbers: dict[str, str] | None = None,
) -> list[FLSParagraph]:
    if not source_dir.exists():
        raise FileNotFoundError(f"FLS source directory not found: {source_dir}")

    if paragraph_numbers is None:
        paragraph_numbers = load_paragraph_numbers()
    all_paragraphs: list[FLSParagraph] = []
    for rst_file in sorted(source_dir.glob("*.rst")):
        if rst_file.name.startswith("_"):
            continue
        all_paragraphs.extend(parse_fls_rst(rst_file, paragraph_numbers=paragraph_numbers))
    return all_paragraphs
