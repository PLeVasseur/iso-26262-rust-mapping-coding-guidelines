#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

from _common import EXIT_RUNTIME_FAIL, EXIT_SUCCESS, repo_root, write_json
from known_good_lib import (
    guideline_id_from_path,
    load_manifest,
    parse_markdown_front_matter,
    save_manifest,
    save_report,
    utc_now,
)

EXAMPLE_HEADING_RE = re.compile(
    r"^##\s+(Compliant|Non-Compliant)\s+Example\s+\d+(?:\s+\(([^)]+)\))?"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build canonical known-good JSON from Markdown")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("benchmarks/known-good/manifest.yaml"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmarks/known-good/canonical"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("benchmarks/known-good/reports/canonical_report.json"),
    )
    return parser.parse_args()


def split_sections(body: str) -> dict[str, list[tuple[str, list[str]]]]:
    sections: dict[str, list[tuple[str, list[str]]]] = {}
    current_heading = ""
    current_lines: list[str] = []

    def commit() -> None:
        nonlocal current_heading, current_lines
        if not current_heading:
            return
        section_name = current_heading.split(":", maxsplit=1)[0].strip()
        sections.setdefault(section_name, []).append((current_heading, current_lines))

    for line in body.splitlines():
        if line.startswith("## "):
            commit()
            current_heading = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)
    commit()
    return sections


def parse_options_line(line: str) -> dict[str, str]:
    stripped = line.strip()
    if not stripped.startswith("- options:"):
        return {}
    payload = stripped.split(":", maxsplit=1)[1].strip()
    result: dict[str, str] = {}
    for token in payload.split(","):
        token = token.strip()
        if not token:
            continue
        if "=" in token:
            key, value = token.split("=", maxsplit=1)
            result[key.strip()] = value.strip()
        else:
            result[token] = "true"
    return result


def parse_example_section(heading: str, lines: list[str]) -> dict[str, Any]:
    match = EXAMPLE_HEADING_RE.match(f"## {heading}")
    example_id = ""
    if match and match.group(2):
        example_id = match.group(2)

    options: dict[str, str] = {}
    description_lines: list[str] = []
    rust_code_lines: list[str] = []
    in_code = False

    for line in lines:
        if line.strip().startswith("```rust"):
            in_code = True
            continue
        if line.strip().startswith("```") and in_code:
            in_code = False
            continue
        if in_code:
            rust_code_lines.append(line.rstrip())
            continue

        parsed_options = parse_options_line(line)
        if parsed_options:
            options.update(parsed_options)
            continue

        description_lines.append(line.rstrip())

    while description_lines and not description_lines[0].strip():
        description_lines.pop(0)
    while description_lines and not description_lines[-1].strip():
        description_lines.pop()

    return {
        "example_id": example_id or "example",
        "status": "",
        "description": "\n".join(description_lines).strip(),
        "rust_code": "\n".join(rust_code_lines).rstrip(),
        "options": options,
    }


def parse_references(lines: list[str]) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        payload = stripped[1:].strip()
        if "::" in payload:
            key, description = payload.split("::", maxsplit=1)
            references.append({"key": key.strip(), "description": description.strip()})
        elif payload and payload != "(none)":
            references.append({"key": payload, "description": payload})
    return references


def parse_citation_signal(lines: list[str], prefix: str) -> list[str]:
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        payload = stripped.split(":", maxsplit=1)[1].strip()
        if not payload or payload == "(none)":
            return []
        return [item.strip() for item in payload.split(",") if item.strip()]
    return []


def build_canonical(markdown: str, fallback_guideline_id: str) -> dict[str, Any]:
    front_matter, body = parse_markdown_front_matter(markdown)
    sections = split_sections(body)

    rule_sections = sections.get("Rule", [])
    rationale_sections = sections.get("Rationale", [])
    reference_sections = sections.get("References", [])
    signal_sections = sections.get("Citation Signals", [])

    rule_text = "\n".join(rule_sections[0][1]).strip() if rule_sections else ""
    rationale_text = "\n".join(rationale_sections[0][1]).strip() if rationale_sections else ""

    compliant_examples: list[dict[str, Any]] = []
    non_compliant_examples: list[dict[str, Any]] = []
    for section_name, entries in sections.items():
        if not section_name.startswith("Compliant Example") and not section_name.startswith(
            "Non-Compliant Example"
        ):
            continue
        for heading, lines in entries:
            parsed = parse_example_section(heading, lines)
            if heading.startswith("Compliant Example"):
                compliant_examples.append(parsed)
            else:
                non_compliant_examples.append(parsed)

    references = parse_references(reference_sections[0][1] if reference_sections else [])
    signal_lines = signal_sections[0][1] if signal_sections else []
    citations = parse_citation_signal(signal_lines, "- citations")
    std_refs = parse_citation_signal(signal_lines, "- std_refs")

    metadata = front_matter.get("metadata") or {}
    guideline_id = str(front_matter.get("guideline_id") or fallback_guideline_id)
    title = str(front_matter.get("title") or "Untitled Guideline")
    source_path = str(front_matter.get("source_path") or "")
    source_sha = str(front_matter.get("source_sha") or "")
    tier = str(front_matter.get("tier") or "extended")

    return {
        "version": 1,
        "guideline_id": guideline_id,
        "title": title,
        "source_path": source_path,
        "source_sha": source_sha,
        "tier": tier,
        "metadata": metadata,
        "rule_text": rule_text,
        "rationale_text": rationale_text,
        "non_compliant_examples": non_compliant_examples,
        "compliant_examples": compliant_examples,
        "references": references,
        "citations": citations,
        "std_refs": std_refs,
        "content_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
    }


def main() -> int:
    args = parse_args()
    root = repo_root()
    manifest_path = root / args.manifest
    if not manifest_path.exists():
        print(f"[known-good-canonical][error] missing manifest: {manifest_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL

    manifest = load_manifest(manifest_path)
    output_dir = root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    built = 0
    missing_md = 0
    for entry in manifest.get("guidelines", []):
        md_rel = str(entry.get("local_md_path") or "").strip()
        if not md_rel:
            missing_md += 1
            continue
        md_path = root / md_rel
        if not md_path.exists():
            missing_md += 1
            continue

        markdown = md_path.read_text(encoding="utf-8")
        fallback_guideline_id = str(entry.get("guideline_id") or guideline_id_from_path(md_rel))
        canonical = build_canonical(markdown, fallback_guideline_id=fallback_guideline_id)

        canonical_rel = Path(args.output_dir) / f"{fallback_guideline_id}.json"
        canonical_path = root / canonical_rel
        write_json(canonical_path, canonical)
        entry["local_canonical_path"] = str(canonical_rel)
        built += 1

    save_manifest(manifest_path, manifest)

    report = {
        "version": 1,
        "generated_at": utc_now(),
        "manifest": str(args.manifest),
        "built_count": built,
        "missing_markdown_count": missing_md,
    }
    save_report(root / args.report, report)

    print(
        "[known-good-canonical] "
        f"built={built} missing_markdown={missing_md} "
        f"manifest={manifest_path.relative_to(root)}"
    )
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
