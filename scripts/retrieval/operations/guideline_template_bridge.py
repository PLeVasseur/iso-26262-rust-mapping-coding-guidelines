from __future__ import annotations

import importlib.util
import re
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_template_modules(guidelines_repo_root: Path) -> tuple[ModuleType, ModuleType]:
    templates = _load_module(
        "guideline_templates_bridge",
        guidelines_repo_root / "scripts" / "common" / "guideline_templates.py",
    )
    pages = _load_module(
        "guideline_pages_bridge",
        guidelines_repo_root / "scripts" / "common" / "guideline_pages.py",
    )
    return templates, pages


@contextmanager
def deterministic_ids(
    templates_module: ModuleType,
    *,
    guideline_id: str,
    seed: str,
) -> Iterator[None]:
    original = getattr(templates_module, "generate_id")
    counters: dict[str, int] = {}

    def _next(prefix: str) -> str:
        index = counters.get(prefix, 0) + 1
        counters[prefix] = index
        if prefix == "gui":
            return guideline_id
        token = f"{seed}_{prefix}_{index}".replace("-", "")
        compact = "".join(ch for ch in token if ch.isalnum())
        return f"{prefix}_{compact[:12].ljust(12, '0')}"

    setattr(templates_module, "generate_id", _next)
    try:
        yield
    finally:
        setattr(templates_module, "generate_id", original)


def build_template_guideline_page(
    *,
    guidelines_repo_root: Path,
    guideline_id: str,
    title: str,
    category: str,
    status: str,
    release: str,
    fls_id: str,
    decidability: str,
    scope: str,
    tags: list[str],
    amplification: str,
    rationale: str,
    non_compliant_examples: list[tuple[str, str]],
    compliant_examples: list[tuple[str, str]],
    bibliography_entries: list[tuple[str, str, str, str]],
    non_compliant_miri_intent: str,
    compliant_miri_intent: str,
) -> str:
    templates, pages = load_template_modules(guidelines_repo_root)
    with deterministic_ids(templates, guideline_id=guideline_id, seed=guideline_id):
        guideline_body = templates.guideline_rst_template(
            guideline_title=title,
            category=category,
            status=status,
            release_begin=release,
            release_end="",
            fls_id=fls_id,
            decidability=decidability,
            scope=scope,
            tags=", ".join(tags),
            amplification=amplification,
            exceptions="",
            rationale=rationale,
            non_compliant_examples=non_compliant_examples,
            compliant_examples=compliant_examples,
            bibliography_entries=bibliography_entries,
        )
    guideline_body = re.sub(
        r"^\s*:release:\s+([^\s]+)-\s*$", r"    :release: \1", guideline_body, flags=re.MULTILINE
    )
    guideline_body = re.sub(
        r"^\s*:fls:\s+.*$",
        f"    :fls: {fls_id}",
        guideline_body,
        flags=re.MULTILINE,
    )
    page = pages.build_guideline_page_content(title, guideline_body)
    return _apply_miri_intents(
        page,
        intents=[non_compliant_miri_intent, compliant_miri_intent],
    )


def _miri_value(intent: str) -> str:
    normalized = str(intent).strip().lower()
    if normalized == "check":
        return ":miri:"
    if normalized == "expect_ub":
        return ":miri: expect_ub"
    if normalized == "skip":
        return ":miri: skip"
    return ""


def _apply_miri_intents(content: str, *, intents: list[str]) -> str:
    lines = content.splitlines()
    out: list[str] = []
    index = 0
    block_index = 0
    while index < len(lines):
        line = lines[index]
        out.append(line)
        if line.strip() == ".. rust-example::":
            intent = intents[block_index] if block_index < len(intents) else ""
            block_index += 1
            option = _miri_value(intent)
            if option:
                indent = " " * (len(line) - len(line.lstrip()) + 3)
                out.append(f"{indent}{option}")
        index += 1
    return "\n".join(out).rstrip() + "\n"


def parse_bibliography_payload(raw_content: str) -> tuple[str, str, str, str] | None:
    try:
        payload: dict[str, Any] = __import__("json").loads(raw_content)
    except Exception:
        return None
    citation_key = str(payload.get("citation_key", "")).strip()
    author = (
        str(payload.get("author", "")).strip()
        or str(payload.get("publisher", "")).strip()
        or str(payload.get("document", "")).strip()
        or str(payload.get("corpus", "")).strip()
        or "Reference"
    )
    title = str(payload.get("title", "")).strip().rstrip(".") or citation_key
    url = str(payload.get("url", "")).strip() or str(payload.get("source_anchor", "")).strip()
    if not citation_key:
        return None
    return citation_key, author, title, url
