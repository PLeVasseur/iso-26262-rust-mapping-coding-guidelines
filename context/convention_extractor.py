"""Convention extraction from curated exemplar .rst files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExemplarConventions:
    """Conventions extracted from a single exemplar .rst file."""

    guideline_id: str
    title: str
    title_format: str
    category: str
    tags: list[str]
    std_roles_used: list[str]
    cite_placements: list[str]
    miri_usage: dict[str, str]
    edition: str
    bibliography_url_patterns: list[str]
    sub_element_prefixes: dict[str, str]
    decidability: str
    scope: str


def _extract_exemplar_conventions(rst_path: Path) -> ExemplarConventions:
    """Parse an exemplar .rst file to extract conventions."""
    text = rst_path.read_text(encoding="utf-8")

    guideline_id = ""
    id_match = re.search(r":id:\s+(gui_\S+)", text)
    if id_match:
        guideline_id = id_match.group(1).strip()

    title = ""
    title_match = re.search(r"^(.+)\n=+\n", text, re.MULTILINE)
    if title_match:
        title = title_match.group(1).strip()

    category = ""
    cat_match = re.search(r":category:\s+(\S+)", text)
    if cat_match:
        category = cat_match.group(1).strip()

    tags: list[str] = []
    tag_match = re.search(r":tags:\s+(.+)", text)
    if tag_match:
        tags = [token.strip() for token in tag_match.group(1).split(",") if token.strip()]

    std_roles = re.findall(r":std:(?:\w+:)?`([^`]+)`", text)

    cite_placements: list[str] = []
    for match in re.finditer(r".{0,40}:cite:`[^`]+`.{0,20}", text):
        cite_placements.append(match.group(0).strip())

    miri_usage: dict[str, str] = {}
    for match in re.finditer(
        r"\.\.\s+(non_compliant_example|compliant_example)::.*?(?=\n\s*\.\.\s+(?:non_compliant_example|compliant_example|bibliography|rationale)::|\Z)",
        text,
        re.DOTALL,
    ):
        block_kind = match.group(1)
        block = match.group(0)
        if ":miri: expect_ub" in block:
            miri_usage[
                "non_compliant" if block_kind == "non_compliant_example" else "compliant"
            ] = "expect_ub"
        elif ":miri:" in block:
            miri_usage[
                "non_compliant" if block_kind == "non_compliant_example" else "compliant"
            ] = "check"

    edition = ""
    edition_match = re.search(r":edition:\s+(\S+)", text)
    if edition_match:
        edition = edition_match.group(1).strip()

    bib_urls = re.findall(r"`(https?://[^`]+)`", text)
    bib_domains = {
        domain.group(1)
        for domain in (re.match(r"https?://([^/]+)", url) for url in bib_urls)
        if domain
    }

    prefixes: dict[str, str] = {}
    for match in re.finditer(r":id:\s+((?:non_compl_ex|compl_ex|bib|rat)_\S+)", text):
        value = match.group(1)
        if value.startswith("non_compl_ex_"):
            prefixes["non_compliant_example"] = "non_compl_ex_"
        elif value.startswith("compl_ex_"):
            prefixes["compliant_example"] = "compl_ex_"
        elif value.startswith("bib_"):
            prefixes["bibliography"] = "bib_"
        elif value.startswith("rat_"):
            prefixes["rationale"] = "rat_"

    decidability = ""
    dec_match = re.search(r":decidability:\s+(\S+)", text)
    if dec_match:
        decidability = dec_match.group(1).strip()

    scope = ""
    scope_match = re.search(r":scope:\s+(\S+)", text)
    if scope_match:
        scope = scope_match.group(1).strip()

    title_format = "descriptive_sentence"
    if title.startswith("Guideline for "):
        title_format = "generic"

    return ExemplarConventions(
        guideline_id=guideline_id,
        title=title,
        title_format=title_format,
        category=category,
        tags=tags,
        std_roles_used=std_roles,
        cite_placements=cite_placements[:5],
        miri_usage=miri_usage,
        edition=edition,
        bibliography_url_patterns=sorted(bib_domains),
        sub_element_prefixes=prefixes,
        decidability=decidability,
        scope=scope,
    )


def extract_all_exemplar_conventions(exemplar_paths: list[Path]) -> list[ExemplarConventions]:
    """Extract conventions for all exemplar paths."""
    conventions: list[ExemplarConventions] = []
    for path in exemplar_paths:
        conventions.append(_extract_exemplar_conventions(path))
    return conventions
