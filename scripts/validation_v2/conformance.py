"""Output conformance validation for rendered .rst guidelines."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from docutils import nodes
from docutils.frontend import OptionParser
from docutils.parsers.rst import Directive, Parser, directives, roles
from docutils.utils import new_document

KNOWN_STD_TYPES = [
    "Option",
    "Result",
    "Vec",
    "String",
    "Box",
    "Arc",
    "Rc",
    "Mutex",
    "RwLock",
    "AtomicBool",
    "AtomicUsize",
    "AtomicI32",
    "AtomicU32",
    "AtomicI64",
    "AtomicU64",
    "AtomicPtr",
    "RefCell",
    "Cell",
    "Pin",
    "MaybeUninit",
    "ManuallyDrop",
    "NonNull",
    "UnsafeCell",
]


class _AnyOptionSpec(dict[str, Any]):
    def __contains__(self, key: object) -> bool:  # pragma: no cover - protocol shim
        return True

    def __getitem__(self, key: str) -> Any:
        return lambda arg: arg


class _PassthroughDirective(Directive):
    required_arguments = 0
    optional_arguments = 10
    final_argument_whitespace = True
    has_content = True
    option_spec = _AnyOptionSpec()

    def run(self) -> list[nodes.Node]:
        return []


_CUSTOM_DIRECTIVES = (
    "guideline",
    "rationale",
    "non_compliant_example",
    "compliant_example",
    "rust-example",
    "bibliography",
    "default-domain",
)


def _passthrough_role(
    _name: str,
    rawtext: str,
    text: str,
    _lineno: int,
    _inliner: Any,
    options: Mapping[str, Any] | None = None,
    _content: Sequence[str] | None = None,
) -> Any:
    del options
    return [nodes.literal(rawtext, text)], []


def _register_docutils_extensions() -> None:
    for name in _CUSTOM_DIRECTIVES:
        directives.register_directive(name, _PassthroughDirective)
    for role_name in ("cite", "bibentry", "std"):
        roles.register_local_role(role_name, cast(Any, _passthrough_role))


def _line_number(text: str, pos: int) -> int:
    return text[:pos].count("\n") + 1


def _collect_rust_example_line_numbers(text: str) -> set[int]:
    lines = text.splitlines()
    marked: set[int] = set()
    in_rust = False
    rust_indent = 0

    for idx, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if stripped.startswith(".. rust-example::"):
            in_rust = True
            rust_indent = indent
            marked.add(idx)
            continue

        if in_rust:
            if stripped.startswith(".. ") and indent <= rust_indent:
                in_rust = False
            else:
                marked.add(idx)
    return marked


def _iter_rust_example_blocks(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    blocks: list[dict[str, Any]] = []

    idx = 0
    while idx < len(lines):
        raw = lines[idx]
        stripped = raw.lstrip()
        indent = len(raw) - len(stripped)
        if not stripped.startswith(".. rust-example::"):
            idx += 1
            continue

        line_no = idx + 1
        idx += 1
        options: list[str] = []
        body_lines: list[str] = []

        while idx < len(lines):
            current = lines[idx]
            current_stripped = current.lstrip()
            current_indent = len(current) - len(current_stripped)
            if current_stripped.startswith(".. ") and current_indent <= indent:
                break
            if current_stripped.startswith(":") and current_indent > indent:
                options.append(current_stripped)
            elif current_indent > indent:
                body_lines.append(current)
            idx += 1

        blocks.append(
            {
                "line": line_no,
                "options": options,
                "body": "\n".join(body_lines),
            }
        )

    return blocks


def _check_citation_consistency(rst_text: str) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    cite_keys = set(re.findall(r":cite:`([^`]+)`", rst_text))
    bib_keys = set(re.findall(r":bibentry:`([^`]+)`", rst_text))

    for key in sorted(cite_keys - bib_keys):
        violations.append(
            {
                "check": "cite_without_bibentry",
                "severity": "error",
                "message": f":cite:`{key}` has no matching :bibentry:",
            }
        )
    for key in sorted(bib_keys - cite_keys):
        violations.append(
            {
                "check": "bibentry_without_cite",
                "severity": "warning",
                "message": f":bibentry:`{key}` is never cited inline",
            }
        )
    return violations


def _check_rst_structure(rst_text: str, filepath: str) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    _register_docutils_extensions()

    parser = Parser()
    settings = OptionParser(components=(Parser,)).get_default_values()
    settings.report_level = 2
    settings.halt_level = 5

    doc = new_document(filepath, settings)
    parser.parse(rst_text, doc)

    for node in doc.traverse(nodes.system_message):
        level = int(node.get("level", 0))
        if level < 2:
            continue
        severity = "warning" if level == 2 else "error"
        violations.append(
            {
                "check": "rst_parse_error",
                "severity": severity,
                "message": node.astext().strip(),
                "line": node.get("line"),
            }
        )
    return violations


def validate_rst_conformance(
    rst_path: Path,
    guideline_id: str,
    convention_spec: dict[str, Any] | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    text = rst_path.read_text(encoding="utf-8")
    violations: list[dict[str, Any]] = []

    std_types = KNOWN_STD_TYPES
    if convention_spec:
        spec_known_types = convention_spec.get("known_types")
        if isinstance(spec_known_types, dict) and spec_known_types:
            std_types = [str(value) for value in spec_known_types.keys() if str(value).strip()]
        elif "std_role_convention" in convention_spec:
            spec_types = convention_spec["std_role_convention"].get("known_types", [])
            if isinstance(spec_types, list) and spec_types:
                std_types = [str(value) for value in spec_types if str(value).strip()]

    for match in re.finditer(r":id:\s+(gui_[A-Za-z0-9]+)", text):
        id_value = match.group(1)
        suffix = id_value.removeprefix("gui_")
        if len(suffix) == 12 and re.fullmatch(r"[a-f0-9]+", suffix):
            violations.append(
                {
                    "check": "id_format_hex_hash",
                    "value": id_value,
                    "reason": (
                        "ID suffix is lowercase hex only and looks fabricated. "
                        "Expected mixed-case alphanumeric output from generate_id()."
                    ),
                    "line": _line_number(text, match.start()),
                }
            )

    wrong_prefixes = {
        "non_gui_": "non_compl_ex_",
        "com_gui_": "compl_ex_",
        "bib_gui_": "bib_",
    }
    for wrong, expected in wrong_prefixes.items():
        for match in re.finditer(rf":id:\s+({re.escape(wrong)}\S+)", text):
            violations.append(
                {
                    "check": "sub_element_prefix",
                    "value": match.group(1),
                    "expected": expected,
                    "line": _line_number(text, match.start()),
                }
            )

    title_match = re.search(r"^(.+)\n=+\n", text, re.MULTILINE)
    if title_match:
        title = title_match.group(1).strip()
        if title.startswith("Guideline for "):
            violations.append(
                {
                    "check": "title_format",
                    "value": title,
                    "reason": "Title is generic; expected descriptive guideline sentence.",
                    "line": _line_number(text, title_match.start()),
                }
            )

    rust_lines = _collect_rust_example_line_numbers(text)
    for type_name in std_types:
        pattern = rf"(?<!:std:)(?<!:)`{re.escape(type_name)}`"
        for match in re.finditer(pattern, text):
            line = _line_number(text, match.start())
            if line in rust_lines:
                continue
            violations.append(
                {
                    "check": "std_role_missing",
                    "type": type_name,
                    "line": line,
                    "context": text[max(0, match.start() - 24) : match.end() + 24].strip(),
                }
            )

    for block in _iter_rust_example_blocks(text):
        options_text = "\n".join(block["options"])
        body_text = str(block["body"])
        if "unsafe" in body_text and ":miri:" not in options_text:
            violations.append(
                {
                    "check": "missing_miri_option",
                    "reason": "rust-example contains unsafe code but no :miri: option.",
                    "line": int(block["line"]),
                    "severity": "warning",
                }
            )
        if ":edition:" not in options_text:
            violations.append(
                {
                    "check": "missing_edition_option",
                    "line": int(block["line"]),
                    "severity": "warning",
                }
            )

    if "evidence_bundle/" in text:
        violations.append(
            {
                "check": "bibliography_internal_path",
                "reason": "Bibliography references evidence_bundle/ internal path.",
            }
        )

    bib_keys = re.findall(r":bibentry:`([^`]+)`", text)
    for key in bib_keys:
        if ":" not in key:
            continue
        key_prefix = key.split(":", 1)[0]
        if guideline_id and key_prefix != guideline_id:
            violations.append(
                {
                    "check": "citation_key_prefix_mismatch",
                    "key": key,
                    "key_prefix": key_prefix,
                    "guideline_id": guideline_id,
                    "reason": "Citation key prefix does not match containing guideline ID.",
                }
            )

    tag_match = re.search(r":tags:\s+(.+)", text)
    if tag_match:
        tags = [part.strip() for part in tag_match.group(1).split(",") if part.strip()]
        for tag in tags:
            if re.fullmatch(r"table1-\d+[a-z]?", tag):
                violations.append(
                    {
                        "check": "tag_iso_derived",
                        "value": tag,
                        "reason": "Use descriptive subject tags, not ISO row tags.",
                    }
                )
            if tag in {"core_docs", "rust_reference", "s0"}:
                violations.append(
                    {
                        "check": "tag_corpus_name",
                        "value": tag,
                        "reason": "Tag must describe guideline content, not pipeline corpus names.",
                    }
                )

    fls_match = re.search(r":fls:\s+(\S+)", text)
    gui_match = re.search(r":id:\s+(gui_\S+)", text)
    if fls_match:
        fls_id = fls_match.group(1)
        if fls_id == "fls_UNRESOLVED":
            violations.append(
                {
                    "check": "fls_id_unresolved",
                    "fls": fls_id,
                    "severity": "warning",
                    "reason": "FLS unresolved placeholder present.",
                }
            )
        elif re.fullmatch(r"fls_[0-9a-f]{8,}", fls_id):
            violations.append(
                {
                    "check": "fls_id_looks_like_hash",
                    "fls": fls_id,
                    "reason": "FLS ID looks like a fabricated hash.",
                }
            )
        elif gui_match:
            gui_id = gui_match.group(1)
            if fls_id.removeprefix("fls_") == gui_id.removeprefix("gui_"):
                violations.append(
                    {
                        "check": "fls_id_mirrors_gui_id",
                        "fls": fls_id,
                        "gui": gui_id,
                        "reason": "FLS ID mirrors guideline ID suffix and appears fabricated.",
                    }
                )

    cat_match = re.search(r":category:\s+(\S+)", text)
    if cat_match and cat_match.group(1) == "mandatory":
        violations.append(
            {
                "check": "category_mandatory_flag",
                "reason": "Category mandatory detected; monitor batch-level distribution.",
                "severity": "warning",
            }
        )

    required_markers = [
        ".. guideline::",
        ":id:",
        ":category:",
        ":status:",
        ":release:",
        ":fls:",
        ":decidability:",
        ":scope:",
        ":tags:",
        ".. rationale::",
        ".. non_compliant_example::",
        ".. compliant_example::",
        "SPDX-License-Identifier",
    ]
    for marker in required_markers:
        if marker not in text:
            violations.append({"check": "missing_structure", "marker": marker})

    if ".. bibliography::" not in text:
        violations.append(
            {
                "check": "missing_structure",
                "marker": ".. bibliography::",
                "severity": "warning",
            }
        )

    violations.extend(_check_citation_consistency(text))
    violations.extend(_check_rst_structure(text, str(rst_path)))

    is_valid = not any(v.get("severity", "error") == "error" for v in violations)
    return is_valid, violations


def validate_batch_conformance(
    conformance_results: list[dict[str, Any]],
) -> tuple[bool, list[dict[str, Any]]]:
    violations: list[dict[str, Any]] = []

    categories = [row.get("category") for row in conformance_results if row.get("category")]
    if categories and all(cat == "mandatory" for cat in categories):
        violations.append(
            {
                "check": "category_all_mandatory",
                "count": len(categories),
                "reason": "All guidelines are mandatory; expected mixed category distribution.",
            }
        )

    all_ids: list[str] = []
    for row in conformance_results:
        ids_found = row.get("ids_found", [])
        if isinstance(ids_found, list):
            all_ids.extend(str(item) for item in ids_found)

    seen: set[str] = set()
    duplicates: set[str] = set()
    for id_value in all_ids:
        if id_value in seen:
            duplicates.add(id_value)
        seen.add(id_value)
    if duplicates:
        violations.append(
            {
                "check": "duplicate_ids_across_files",
                "ids": sorted(duplicates),
            }
        )

    return len(violations) == 0, violations
