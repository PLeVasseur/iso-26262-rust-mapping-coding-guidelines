#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from _common import EXIT_RUNTIME_FAIL, EXIT_SUCCESS, repo_root
from known_good_lib import (
    guideline_id_from_path,
    load_manifest,
    markdown_front_matter,
    parse_guideline_rst,
    save_manifest,
    save_report,
    utc_now,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate known-good rST guidelines to Markdown")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("benchmarks/known-good/manifest.yaml"),
    )
    parser.add_argument("--output-root", type=Path, default=Path("benchmarks/known-good"))
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("benchmarks/known-good/reports/translation_report.json"),
    )
    return parser.parse_args()


def render_example_heading(example: dict[str, Any], prefix: str, ordinal: int) -> str:
    example_id = str(example.get("example_id") or "").strip()
    if example_id:
        return f"## {prefix} Example {ordinal} ({example_id})"
    return f"## {prefix} Example {ordinal}"


def render_example(example: dict[str, Any], prefix: str, ordinal: int) -> list[str]:
    lines = [render_example_heading(example, prefix, ordinal), ""]
    description = str(example.get("description") or "").strip()
    if description:
        lines.append(description)
        lines.append("")

    options = example.get("options") or {}
    if options:
        option_text = ", ".join(f"{key}={value}" for key, value in sorted(options.items()))
        lines.append(f"- options: {option_text}")
        lines.append("")

    rust_code = str(example.get("rust_code") or "").rstrip()
    if rust_code:
        lines.append("```rust")
        lines.append(rust_code)
        lines.append("```")
        lines.append("")
    return lines


def render_markdown(entry: dict[str, Any], parsed: dict[str, Any], source_sha: str) -> str:
    metadata = parsed.get("metadata") or {}
    front_matter = {
        "guideline_id": str(entry.get("guideline_id") or ""),
        "source_path": str(entry.get("source_path") or ""),
        "source_sha": source_sha,
        "tier": str(entry.get("tier") or "extended"),
        "title": str(parsed.get("title") or entry.get("title") or "Untitled Guideline"),
        "metadata": metadata,
    }

    lines = [markdown_front_matter(front_matter), ""]
    title = str(parsed.get("title") or entry.get("title") or "Untitled Guideline")
    lines.append(f"# {title}")
    lines.append("")

    lines.append("## Rule")
    lines.append("")
    rule_text = str(parsed.get("rule_text") or "").strip()
    lines.append(rule_text if rule_text else "(no rule body extracted)")
    lines.append("")

    lines.append("## Rationale")
    lines.append("")
    rationale_text = str(parsed.get("rationale_text") or "").strip()
    lines.append(rationale_text if rationale_text else "(no rationale extracted)")
    lines.append("")

    non_compliant_examples = parsed.get("non_compliant_examples") or []
    for ordinal, example in enumerate(non_compliant_examples, start=1):
        lines.extend(render_example(example, "Non-Compliant", ordinal))

    compliant_examples = parsed.get("compliant_examples") or []
    for ordinal, example in enumerate(compliant_examples, start=1):
        lines.extend(render_example(example, "Compliant", ordinal))

    lines.append("## References")
    lines.append("")
    references = parsed.get("references") or []
    if references:
        for reference in references:
            key = str(reference.get("key") or "").strip()
            description = str(reference.get("description") or "").strip()
            if key and description:
                lines.append(f"- {key} :: {description}")
            elif key:
                lines.append(f"- {key}")
    else:
        lines.append("- (none)")
    lines.append("")

    citations = parsed.get("citations") or []
    std_refs = parsed.get("std_refs") or []
    lines.append("## Citation Signals")
    lines.append("")
    lines.append(f"- citations: {', '.join(citations) if citations else '(none)'}")
    lines.append(f"- std_refs: {', '.join(std_refs) if std_refs else '(none)'}")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    root = repo_root()
    manifest_path = root / args.manifest
    if not manifest_path.exists():
        print(f"[known-good-translate][error] missing manifest: {manifest_path.relative_to(root)}")
        return EXIT_RUNTIME_FAIL

    manifest = load_manifest(manifest_path)
    source_sha = str(manifest.get("source_sha") or "")
    translated = 0
    missing = 0

    for entry in manifest.get("guidelines", []):
        source_rel = str(entry.get("local_rst_path") or "").strip()
        if not source_rel:
            missing += 1
            continue
        source_path = root / source_rel
        if not source_path.exists():
            missing += 1
            continue

        source_text = source_path.read_text(encoding="utf-8")
        parsed = parse_guideline_rst(source_text, fallback_title=str(entry.get("title") or ""))

        chapter = str(entry.get("chapter") or "misc")
        guideline_id = str(entry.get("guideline_id") or "").strip()
        if not guideline_id:
            guideline_id = guideline_id_from_path(str(entry.get("source_path") or ""))

        md_rel = Path(args.output_root) / "markdown" / chapter / f"{guideline_id}.md"
        md_path = root / md_rel
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(entry, parsed, source_sha), encoding="utf-8")

        entry["local_md_path"] = str(md_rel)
        translated += 1

    save_manifest(manifest_path, manifest)
    report = {
        "version": 1,
        "generated_at": utc_now(),
        "manifest": str(args.manifest),
        "translated_count": translated,
        "missing_source_count": missing,
    }
    report_path = root / args.report
    save_report(report_path, report)

    print(
        "[known-good-translate] "
        f"translated={translated} missing={missing} "
        f"manifest={manifest_path.relative_to(root)}"
    )
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
