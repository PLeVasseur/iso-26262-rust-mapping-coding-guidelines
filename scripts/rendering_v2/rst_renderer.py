"""Convention-aware standalone RST renderer for Step 2.

The renderer owns mechanical formatting (IDs, directive structure, prefixes,
namespacing, options, and indentation). LLM outputs provide content only.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.import_utils import import_from_repo

DEFAULT_EDITION = "2021"
CITATION_PLACEMENT_POLICY = "renderer_injected"


@dataclass(slots=True)
class RendererInput:
    """Typed input to the RST renderer."""

    title: str
    guideline_text: str
    rationale_text: str
    non_compliant_narrative: str
    non_compliant_code: str
    compliant_narrative: str
    compliant_code: str
    bibliography_rows: list[dict[str, Any]]
    non_compliant_mode: str
    compliant_mode: str
    non_compliant_miri_intent: str
    compliant_miri_intent: str
    category: str
    normative_strength: str
    decidability: str
    scope: str
    tags: list[str]
    citation_keys_used: list[str]
    prompt_id: str
    exemplar_ids_used: list[str]
    release: str = "latest"


@dataclass(slots=True)
class RenderArtifacts:
    """Rendered output plus deterministic IDs and citation mapping."""

    rst: str
    guideline_id: str
    rationale_id: str
    fls_placeholder_id: str
    citation_key_map: dict[str, str]


def _import_guideline_templates(guidelines_repo_root: Path):
    return import_from_repo(
        "guideline_templates_step2",
        guidelines_repo_root / "scripts" / "common" / "guideline_templates.py",
    )


def _seed_from_prompt_id(prompt_id: str) -> int:
    digest = hashlib.sha256(prompt_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _generate_upstream_ids(prompt_id: str, guidelines_repo_root: Path) -> tuple[str, str, str]:
    """Generate deterministic IDs in upstream format.

    The upstream `generate_id` uses global random state. We save/restore global
    state so this renderer is deterministic and does not pollute process RNG.
    """

    guideline_templates = _import_guideline_templates(guidelines_repo_root)
    generate_id = guideline_templates.generate_id

    local_rng = random.Random(_seed_from_prompt_id(prompt_id))
    global_state = random.getstate()
    try:
        random.setstate(local_rng.getstate())
        guideline_id = generate_id("gui")
        rationale_id = generate_id("rat")
    finally:
        random.setstate(global_state)

    fls_placeholder = f"fls_{hashlib.sha256(prompt_id.encode('utf-8')).hexdigest()[:12]}"
    return guideline_id, rationale_id, fls_placeholder


def _normalize_citation_key(raw_key: str, guideline_id: str) -> str:
    """Normalize keys to canonical renderer output.

    Exemplar DB data stores flat bibliography keys and links them by guideline.
    Writer outputs can be flat or already prefixed; renderer output is canonical
    namespaced form `{guideline_id}:{suffix}`.
    """

    key = raw_key.strip()
    if not key:
        return ""
    if ":" in key:
        key = key.split(":", 1)[1].strip()
    return f"{guideline_id}:{key}"


def _indent_block(text: str, spaces: int) -> str:
    indent = " " * spaces
    lines = text.splitlines() if text else [""]
    return "\n".join(f"{indent}{line}" if line else "" for line in lines)


def _infer_miri(code: str, declared_intent: str) -> str:
    intent = (declared_intent or "none").strip().lower()
    if intent in {"expect_ub", "check"}:
        return intent
    return "check" if "unsafe" in code else "none"


def _build_rust_example_options(mode: str, miri_intent: str, edition: str) -> list[str]:
    normalized_mode = (mode or "runnable").strip().lower()
    lines = [f"         :edition: {edition}"]

    if normalized_mode == "compile_fail":
        lines.append("         :compile_fail:")
    elif normalized_mode == "no_run":
        lines.append("         :no_run:")
    elif normalized_mode == "should_panic":
        lines.append("         :should_panic:")

    if miri_intent == "expect_ub":
        lines.append("         :miri: expect_ub")
    elif miri_intent == "check":
        lines.append("         :miri:")

    return lines


def _build_bibliography_rows(
    rows: list[dict[str, Any]], guideline_id: str
) -> tuple[list[str], dict[str, str], list[str]]:
    rendered_rows: list[str] = []
    key_map: dict[str, str] = {}
    ordered_keys: list[str] = []

    for row in rows:
        raw_key = str(row.get("citation_key", "")).strip()
        if not raw_key:
            continue

        namespaced_key = _normalize_citation_key(raw_key, guideline_id)
        if not namespaced_key:
            continue

        key_map[raw_key] = namespaced_key
        if namespaced_key not in ordered_keys:
            ordered_keys.append(namespaced_key)

        locator = row.get("locator", "")
        locator_url = ""
        if isinstance(locator, dict):
            locator_url = str(locator.get("url", "")).strip()
        elif isinstance(locator, str) and locator.startswith("http"):
            locator_url = locator.strip()

        url = str(row.get("url", "") or locator_url).strip()
        if not url or "evidence_bundle/" in url:
            url = "URL_UNRESOLVED"

        description = str(row.get("title", "") or row.get("description", "")).strip()
        if not description:
            description = "See referenced standard."

        rendered_rows.append(
            f"         * - :bibentry:`{namespaced_key}`\n           - `{description} <{url}>`__"
        )

    return rendered_rows, key_map, ordered_keys


def _inject_cites_if_missing(text: str, ordered_keys: list[str]) -> str:
    body = text.strip()
    if not ordered_keys or ":cite:`" in body:
        return body
    citations = " ".join(f":cite:`{key}`" for key in ordered_keys)
    return f"{body} {citations}".strip()


def _normalize_inline_cites(text: str, guideline_id: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return f":cite:`{_normalize_citation_key(match.group(1), guideline_id)}`"

    return re.sub(r":cite:`([^`]+)`", repl, text)


def _ensure_edition_on_embedded_rust_examples(text: str, edition: str) -> str:
    pattern = re.compile(
        r"(?m)^(?P<indent>[ \t]*)\.\. rust-example::\n(?P<opts>(?:[ \t]+:[^\n]*\n)*)"
    )

    def repl(match: re.Match[str]) -> str:
        indent = match.group("indent")
        opts = match.group("opts")
        if ":edition:" in opts:
            return match.group(0)
        option_indent = indent + "   "
        return f"{indent}.. rust-example::\n{option_indent}:edition: {edition}\n{opts}"

    return pattern.sub(repl, text)


def render_guideline_rst(
    inp: RendererInput,
    guidelines_repo_root: Path,
    edition: str = DEFAULT_EDITION,
) -> RenderArtifacts:
    """Render guideline RST from typed input and return render artifacts."""

    guideline_id, rationale_id, fls_placeholder = _generate_upstream_ids(
        inp.prompt_id,
        guidelines_repo_root,
    )
    suffix = guideline_id.removeprefix("gui_")
    non_compliant_id = f"non_compl_ex_{suffix}"
    compliant_id = f"compl_ex_{suffix}"
    bibliography_id = f"bib_{suffix}"

    bibliography_rows, citation_key_map, bibliography_keys = _build_bibliography_rows(
        inp.bibliography_rows,
        guideline_id,
    )

    for key in inp.citation_keys_used:
        normalized = _normalize_citation_key(key, guideline_id)
        if normalized:
            citation_key_map.setdefault(key, normalized)
            if normalized not in bibliography_keys:
                bibliography_keys.append(normalized)

    tags = ", ".join(tag.strip() for tag in inp.tags if tag.strip()) or "general"

    non_compliant_miri = _infer_miri(inp.non_compliant_code, inp.non_compliant_miri_intent)
    compliant_miri = _infer_miri(inp.compliant_code, inp.compliant_miri_intent)

    non_compliant_options = _build_rust_example_options(
        inp.non_compliant_mode,
        non_compliant_miri,
        edition,
    )
    compliant_options = _build_rust_example_options(inp.compliant_mode, compliant_miri, edition)

    guideline_text = _normalize_inline_cites(inp.guideline_text, guideline_id)
    rationale_text = _normalize_inline_cites(inp.rationale_text, guideline_id)
    non_compliant_narrative = _normalize_inline_cites(inp.non_compliant_narrative, guideline_id)
    compliant_narrative = _normalize_inline_cites(inp.compliant_narrative, guideline_id)

    guideline_text = _inject_cites_if_missing(guideline_text, bibliography_keys)
    rationale_text = _inject_cites_if_missing(rationale_text, bibliography_keys)
    non_compliant_narrative = _inject_cites_if_missing(non_compliant_narrative, bibliography_keys)
    compliant_narrative = _inject_cites_if_missing(compliant_narrative, bibliography_keys)

    guideline_text = _ensure_edition_on_embedded_rust_examples(guideline_text, edition)
    rationale_text = _ensure_edition_on_embedded_rust_examples(rationale_text, edition)
    non_compliant_narrative = _ensure_edition_on_embedded_rust_examples(
        non_compliant_narrative, edition
    )
    compliant_narrative = _ensure_edition_on_embedded_rust_examples(compliant_narrative, edition)

    lines = [
        ".. SPDX-License-Identifier: MIT OR Apache-2.0",
        "   SPDX-FileCopyrightText: The Coding Guidelines Subcommittee Contributors",
        "",
        ".. default-domain:: coding-guidelines",
        "",
        inp.title.strip() or f"Guideline for {inp.prompt_id}",
    ]

    heading = lines[-1]
    lines.extend(
        [
            "=" * len(heading),
            "",
            f".. guideline:: {heading}",
            f"   :id: {guideline_id}",
            f"   :category: {(inp.category or 'advisory').strip().lower()}",
            "   :status: draft",
            f"   :release: {(inp.release or 'latest').strip()}",
            f"   :fls: {fls_placeholder}",
            f"   :decidability: {(inp.decidability or 'undecidable').strip().lower()}",
            f"   :scope: {(inp.scope or 'system').strip().lower()}",
            f"   :tags: {tags}",
            "",
            _indent_block(guideline_text, 3),
            "",
            "   .. rationale::",
            f"      :id: {rationale_id}",
            "      :status: draft",
            "",
            _indent_block(rationale_text, 6),
            "",
            "   .. non_compliant_example::",
            f"      :id: {non_compliant_id}",
            "      :status: draft",
            "",
            _indent_block(non_compliant_narrative.strip(), 6),
            "",
            "      .. rust-example::",
            *non_compliant_options,
            "",
            _indent_block(inp.non_compliant_code.rstrip(), 9),
            "",
            "   .. compliant_example::",
            f"      :id: {compliant_id}",
            "      :status: draft",
            "",
            _indent_block(compliant_narrative.strip(), 6),
            "",
            "      .. rust-example::",
            *compliant_options,
            "",
            _indent_block(inp.compliant_code.rstrip(), 9),
            "",
            "   .. bibliography::",
            f"      :id: {bibliography_id}",
            "      :status: draft",
            "",
            "      .. list-table::",
            "         :header-rows: 0",
            "         :widths: auto",
            "         :class: bibliography-table",
            "",
        ]
    )

    if bibliography_rows:
        lines.extend(bibliography_rows)

    rendered = "\n".join(lines).rstrip() + "\n"

    return RenderArtifacts(
        rst=rendered,
        guideline_id=guideline_id,
        rationale_id=rationale_id,
        fls_placeholder_id=fls_placeholder,
        citation_key_map=citation_key_map,
    )


def _render_guideline_rst(
    inp: RendererInput,
    guidelines_repo_root: Path,
    edition: str = DEFAULT_EDITION,
) -> str:
    """Compatibility wrapper returning only the rendered RST string."""

    return render_guideline_rst(inp, guidelines_repo_root, edition).rst


def serialize_citation_key_map(citation_map: dict[str, dict[str, str]], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(
            {
                "citation_placement_policy": CITATION_PLACEMENT_POLICY,
                "guidelines": citation_map,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
