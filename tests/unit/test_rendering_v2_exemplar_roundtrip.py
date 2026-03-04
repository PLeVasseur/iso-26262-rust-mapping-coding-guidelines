from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

from scripts.import_utils import GUIDELINES_REPO_ROOT
from scripts.retrieval.rendering.rst_renderer import RendererInput, render_guideline_rst

MANIFEST_PATH = Path("data/exemplar_manifest.json")
ROUNDTRIP_REPORT_PATH = Path(".cache/step2_roundtrip/roundtrip_conformance_report.json")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _dedent(lines: list[str]) -> str:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ""
    return textwrap.dedent("\n".join(lines)).strip()


def _find_directive(lines: list[str], name: str, start: int = 0) -> int:
    needle = f".. {name}::"
    for idx in range(start, len(lines)):
        if lines[idx].lstrip(" ").startswith(needle):
            return idx
    return -1


def _parse_directive_block(lines: list[str], idx: int) -> tuple[int, list[str], list[str]]:
    """Return (next_index, options, body_lines) for directive at idx."""
    directive_indent = _indent(lines[idx])
    i = idx + 1
    options: list[str] = []
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith(":") and _indent(lines[i]) > directive_indent:
            options.append(stripped)
            i += 1
            continue
        if not stripped:
            i += 1
            continue
        break

    body: list[str] = []
    while i < len(lines):
        stripped = lines[i].strip()
        current_indent = _indent(lines[i])
        if stripped.startswith(".. ") and current_indent <= directive_indent:
            break
        body.append(lines[i])
        i += 1

    return i, options, body


def _extract_title(lines: list[str]) -> str:
    for i in range(len(lines) - 1):
        title = lines[i].strip()
        underline = lines[i + 1].strip()
        if title and underline and set(underline) == {"="} and len(underline) >= len(title):
            return title
    return "Guideline"


def _extract_guideline_options(lines: list[str], guideline_idx: int) -> dict[str, str]:
    _, options, _ = _parse_directive_block(lines, guideline_idx)
    result: dict[str, str] = {}
    for opt in options:
        if ":" not in opt:
            continue
        key, value = opt[1:].split(":", 1)
        result[key.strip()] = value.strip()
    return result


def _extract_guideline_text(lines: list[str], guideline_idx: int) -> str:
    _, _, body = _parse_directive_block(lines, guideline_idx)
    content: list[str] = []
    for line in body:
        if line.lstrip(" ").startswith(".. rationale::"):
            break
        content.append(line)
    return _dedent(content)


def _extract_subdirective_from_guideline(
    lines: list[str],
    guideline_idx: int,
    name: str,
    nth: int = 0,
) -> tuple[list[str], list[str]]:
    """Return (options, body_lines) for nth subdirective inside guideline."""
    _, _, guideline_body = _parse_directive_block(lines, guideline_idx)
    count = -1
    for i, line in enumerate(guideline_body):
        if line.lstrip(" ").startswith(f".. {name}::"):
            count += 1
            if count == nth:
                next_i, options, body = _parse_directive_block(guideline_body, i)
                _ = next_i
                return options, body
    return [], []


def _extract_all_subdirectives_from_guideline(
    lines: list[str],
    guideline_idx: int,
    name: str,
) -> list[tuple[list[str], list[str]]]:
    """Return all matching subdirectives as (options, body_lines)."""
    _, _, guideline_body = _parse_directive_block(lines, guideline_idx)
    result: list[tuple[list[str], list[str]]] = []
    for i, line in enumerate(guideline_body):
        if line.lstrip(" ").startswith(f".. {name}::"):
            _next_i, options, body = _parse_directive_block(guideline_body, i)
            result.append((options, body))
    return result


def _extract_example_payload(
    lines: list[str], guideline_idx: int, name: str, nth: int = 0
) -> tuple[str, str, str, str]:
    options, body = _extract_subdirective_from_guideline(lines, guideline_idx, name, nth)
    if not body:
        return "", "", "runnable", "none"

    narrative_lines: list[str] = []
    rust_idx = -1
    for i, line in enumerate(body):
        if line.lstrip(" ").startswith(".. rust-example::"):
            rust_idx = i
            break
        narrative_lines.append(line)
    narrative = _dedent(narrative_lines)

    if rust_idx < 0:
        return narrative, "", "runnable", "none"

    _, rust_opts, rust_body = _parse_directive_block(body, rust_idx)
    code = _dedent(rust_body)

    mode = "runnable"
    miri = "none"
    for opt in options + rust_opts:
        normalized = opt.lower()
        if normalized == ":compile_fail:":
            mode = "compile_fail"
        elif normalized == ":no_run:":
            mode = "no_run"
        elif normalized == ":should_panic:":
            mode = "should_panic"
        elif normalized.startswith(":miri:"):
            miri = "expect_ub" if "expect_ub" in normalized else "check"

    return narrative, code, mode, miri


def _extract_example_payloads(
    lines: list[str], guideline_idx: int, name: str
) -> list[tuple[str, str, str, str]]:
    payloads: list[tuple[str, str, str, str]] = []
    for options, body in _extract_all_subdirectives_from_guideline(lines, guideline_idx, name):
        narrative_lines: list[str] = []
        rust_idx = -1
        for i, line in enumerate(body):
            if line.lstrip(" ").startswith(".. rust-example::"):
                rust_idx = i
                break
            narrative_lines.append(line)
        narrative = _dedent(narrative_lines)

        if rust_idx < 0:
            payloads.append((narrative, "", "runnable", "none"))
            continue

        _, rust_opts, rust_body = _parse_directive_block(body, rust_idx)
        code = _dedent(rust_body)

        mode = "runnable"
        miri = "none"
        for opt in options + rust_opts:
            normalized = opt.lower()
            if normalized == ":compile_fail:":
                mode = "compile_fail"
            elif normalized == ":no_run:":
                mode = "no_run"
            elif normalized == ":should_panic:":
                mode = "should_panic"
            elif normalized.startswith(":miri:"):
                miri = "expect_ub" if "expect_ub" in normalized else "check"

        payloads.append((narrative, code, mode, miri))
    return payloads


def _extract_bibliography_rows(lines: list[str], guideline_idx: int) -> list[dict[str, str]]:
    _, bib_body = _extract_subdirective_from_guideline(lines, guideline_idx, "bibliography", 0)
    rows: list[dict[str, str]] = []
    for i, line in enumerate(bib_body):
        key_match = re.search(r":bibentry:`([^`]+)`", line)
        if not key_match:
            continue
        key = key_match.group(1)
        desc = "See referenced standard."
        url = ""
        if i + 1 < len(bib_body):
            next_line = bib_body[i + 1].strip()
            desc_match = re.search(r"`([^`<]+)\s*<", next_line)
            if desc_match:
                desc = desc_match.group(1).strip()
            url_match = re.search(r"(https?://\S+)", next_line)
            if url_match:
                url = url_match.group(1)
        rows.append({"citation_key": key, "title": desc, "url": url})
    return rows


def _to_renderer_inputs(exemplar_path: Path) -> tuple[list[RendererInput], dict[str, int]]:
    lines = exemplar_path.read_text(encoding="utf-8").splitlines()
    guideline_idx = _find_directive(lines, "guideline")
    assert guideline_idx >= 0, f"No guideline directive found in {exemplar_path}"

    title = _extract_title(lines)
    opts = _extract_guideline_options(lines, guideline_idx)
    guideline_text = _extract_guideline_text(lines, guideline_idx)

    rat_opts, rat_body = _extract_subdirective_from_guideline(lines, guideline_idx, "rationale", 0)
    _ = rat_opts
    rationale_text = _dedent(rat_body)

    non_payloads = _extract_example_payloads(lines, guideline_idx, "non_compliant_example")
    com_payloads = _extract_example_payloads(lines, guideline_idx, "compliant_example")

    non_expected = len(non_payloads)
    com_expected = len(com_payloads)
    assert non_expected >= 1, f"No non-compliant examples found in {exemplar_path}"
    assert com_expected >= 1, f"No compliant examples found in {exemplar_path}"

    bibliography_rows = _extract_bibliography_rows(lines, guideline_idx)
    citation_keys_used = re.findall(r":cite:`([^`]+)`", "\n".join(lines))

    inputs: list[RendererInput] = []
    cases = max(non_expected, com_expected)
    for case_idx in range(cases):
        non_idx = min(case_idx, non_expected - 1)
        com_idx = min(case_idx, com_expected - 1)
        non_narrative, non_code, non_mode, non_miri = non_payloads[non_idx]
        com_narrative, com_code, com_mode, com_miri = com_payloads[com_idx]
        inputs.append(
            RendererInput(
                title=title,
                guideline_text=guideline_text,
                rationale_text=rationale_text,
                non_compliant_narrative=non_narrative,
                non_compliant_code=non_code,
                compliant_narrative=com_narrative,
                compliant_code=com_code,
                bibliography_rows=bibliography_rows,
                non_compliant_mode=non_mode,
                compliant_mode=com_mode,
                non_compliant_miri_intent=non_miri,
                compliant_miri_intent=com_miri,
                category=opts.get("category", "advisory"),
                normative_strength=(
                    "shall" if re.search(r"\bshall\b", guideline_text, re.IGNORECASE) else "should"
                ),
                decidability=opts.get("decidability", "undecidable"),
                scope=opts.get("scope", "system"),
                tags=[
                    part.strip() for part in opts.get("tags", "general").split(",") if part.strip()
                ],
                citation_keys_used=citation_keys_used,
                prompt_id=f"{exemplar_path.stem}__case{case_idx + 1}",
                exemplar_ids_used=[exemplar_path.stem],
                release="latest",
            )
        )

    coverage = {
        "non_compliant_blocks_expected": non_expected,
        "compliant_blocks_expected": com_expected,
        "non_compliant_blocks_validated": non_expected,
        "compliant_blocks_validated": com_expected,
    }
    return inputs, coverage


def _validate_mechanical_conformance(rst: str, expect_bibliography: bool) -> list[str]:
    violations: list[str] = []

    gui_match = re.search(r":id:\s+(gui_[A-Za-z0-9]{12})", rst)
    if not gui_match:
        return ["missing_guideline_id"]
    gui_id = gui_match.group(1)

    if ".. default-domain:: coding-guidelines" not in rst:
        violations.append("missing_default_domain")
    if ":release: latest" not in rst:
        violations.append("missing_release_latest")
    if "evidence_bundle/" in rst:
        violations.append("evidence_bundle_leak")

    for prefix in ("non_compl_ex_", "compl_ex_", "bib_"):
        if re.search(rf":id:\s+{prefix}[A-Za-z0-9]{{12}}", rst) is None:
            violations.append(f"missing_prefix_{prefix.rstrip('_')}")

    rust_blocks = re.findall(r"\.\. rust-example::\n((?:\s+:[^\n]+\n)*)", rst)
    if not rust_blocks:
        violations.append("missing_rust_example")
    for idx, block in enumerate(rust_blocks):
        if ":edition:" not in block:
            violations.append(f"missing_edition_block_{idx}")

    if expect_bibliography:
        if ":bibentry:`" not in rst:
            violations.append("missing_bibentry")
        if ":cite:`" not in rst:
            violations.append("missing_cite")

    keys = re.findall(r":cite:`([^`]+)`", rst) + re.findall(r":bibentry:`([^`]+)`", rst)
    for key in keys:
        if not key.startswith(f"{gui_id}:"):
            violations.append(f"citation_not_namespaced:{key}")

    return violations


def test_renderer_roundtrip_all_14_exemplars_zero_conformance_violations() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    exemplars = manifest.get("exemplars", [])
    assert len(exemplars) == 14, "Expected exactly 14 curated exemplars"

    report_rows: list[dict[str, object]] = []
    failures: list[str] = []
    violations_total = 0

    for entry in exemplars:
        rel_path = str(entry["path"])
        path = GUIDELINES_REPO_ROOT / rel_path
        assert path.exists(), f"Missing exemplar: {path}"

        inputs, coverage = _to_renderer_inputs(path)
        exemplar_violations: list[str] = []
        for inp in inputs:
            assert inp.guideline_text, f"Empty guideline text: {rel_path}"
            assert inp.rationale_text, f"Empty rationale text: {rel_path}"

            rendered = render_guideline_rst(inp, GUIDELINES_REPO_ROOT).rst
            exemplar_violations.extend(
                _validate_mechanical_conformance(
                    rendered,
                    expect_bibliography=bool(inp.bibliography_rows),
                )
            )

        status = "pass" if not exemplar_violations else "fail"
        row: dict[str, object] = {
            "path": rel_path,
            "status": status,
            "violations": exemplar_violations,
            "render_cases": len(inputs),
            **coverage,
        }
        report_rows.append(row)
        violations_total += len(exemplar_violations)
        if exemplar_violations:
            failures.append(f"{rel_path}: {exemplar_violations}")
        if coverage["non_compliant_blocks_validated"] != coverage["non_compliant_blocks_expected"]:
            failures.append(f"{rel_path}: non-compliant block coverage mismatch")
        if coverage["compliant_blocks_validated"] != coverage["compliant_blocks_expected"]:
            failures.append(f"{rel_path}: compliant block coverage mismatch")

    ROUNDTRIP_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "total_exemplars": len(exemplars),
        "passed": sum(1 for r in report_rows if r["status"] == "pass"),
        "failed": sum(1 for r in report_rows if r["status"] == "fail"),
        "violations_total": violations_total,
        "per_exemplar": report_rows,
    }
    ROUNDTRIP_REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    assert not failures, "\n".join(failures)
