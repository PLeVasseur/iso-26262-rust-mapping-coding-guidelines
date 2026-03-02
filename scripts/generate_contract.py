#!/usr/bin/env python3
"""Generate integration contract for a completed step."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PIPELINE_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = PIPELINE_ROOT / ".cache" / "step_contracts"
DEVIATIONS_FILE = PIPELINE_ROOT / "STEP_DEVIATIONS.md"


def _purpose_for_path(path: str) -> str:
    value = path.lower()
    if value.startswith("config/"):
        return "Define policy/configuration required by this step."
    if value.startswith("scripts/"):
        return "Add executable runtime logic for step behavior."
    if value.startswith("tests/"):
        return "Add automated verification coverage for step invariants."
    if value.startswith("docs/"):
        return "Document rationale, operator guidance, and recovery details."
    if value.endswith(".md"):
        return "Document step behavior and constraints."
    return "Add step artifact required for integration handoff."


def _change_for_path(path: str) -> str:
    value = path.lower()
    if value.startswith("config/"):
        return "Update policy/config values to align with new step behavior."
    if value.startswith("scripts/"):
        return "Refine runtime logic to enforce step contracts and checks."
    if value.startswith("tests/"):
        return "Expand tests to validate new behavior and prevent regressions."
    if value.startswith("docs/"):
        return "Revise operational documentation for current implementation state."
    if value == "step_deviations.md":
        return "Record implementation deviations and known constraints for handoff."
    return "Update existing artifact to remain compatible with this step output."


def _validate_contract_entries(contract: dict[str, Any]) -> None:
    missing: list[str] = []
    for entry in contract.get("files_created", []):
        if not str(entry.get("purpose", "")).strip():
            missing.append(f"files_created:{entry.get('path', '<unknown>')}:purpose")
    for entry in contract.get("files_modified", []):
        if not str(entry.get("change", "")).strip():
            missing.append(f"files_modified:{entry.get('path', '<unknown>')}:change")
    if missing:
        raise ValueError("Contract metadata incomplete: " + ", ".join(missing))


def _git_diff_stat(prev_tag: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    result = subprocess.run(
        ["git", "diff", "--name-status", prev_tag, "HEAD"],
        capture_output=True,
        text=True,
        cwd=PIPELINE_ROOT,
        check=False,
    )
    created: list[dict[str, str]] = []
    modified: list[dict[str, str]] = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) < 2:
            continue
        status, filepath = parts[0].strip(), parts[1].strip()
        if status == "A":
            created.append({"path": filepath, "purpose": _purpose_for_path(filepath)})
        elif status in {"M", "R"}:
            modified.append({"path": filepath, "change": _change_for_path(filepath)})
    return created, modified


def _format_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    params: list[str] = []
    for arg in node.args.args:
        if arg.arg == "self":
            continue
        ann = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
        params.append(f"{arg.arg}{ann}")
    if node.args.vararg:
        params.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg:
        params.append(f"**{node.args.kwarg.arg}")
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"({', '.join(params)}){ret}"


def _extract_api_surface(py_files: list[str]) -> list[dict[str, Any]]:
    api: list[dict[str, Any]] = []
    for filepath in py_files:
        full_path = PIPELINE_ROOT / filepath
        if not full_path.exists() or not filepath.endswith(".py"):
            continue
        try:
            tree = ast.parse(full_path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        module_name = filepath.replace("/", ".").replace(".py", "")
        if module_name.startswith("scripts."):
            module_name = module_name[len("scripts.") :]

        functions: list[dict[str, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("__") and node.name != "__init__":
                    continue
                functions.append({"name": node.name, "signature": _format_signature(node)})
            elif isinstance(node, ast.ClassDef):
                functions.append({"name": node.name, "signature": f"class {node.name}"})

        if functions:
            api.append({"module": module_name, "functions": functions})
    return api


def _list_new_configs(created_files: list[dict[str, str]]) -> list[dict[str, str]]:
    configs: list[dict[str, str]] = []
    for entry in created_files:
        path = PIPELINE_ROOT / entry["path"]
        if path.suffix not in {".yaml", ".yml", ".json"} or not path.exists():
            continue
        preview = path.read_text(encoding="utf-8")[:500]
        configs.append({"path": entry["path"], "preview": preview})
    return configs


def _parse_deviations() -> tuple[list[str], list[str]]:
    deviations: list[str] = []
    known_issues: list[str] = []
    if not DEVIATIONS_FILE.exists():
        return deviations, known_issues

    content = DEVIATIONS_FILE.read_text(encoding="utf-8")
    current_section: str | None = None
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("## deviations"):
            current_section = "deviations"
            continue
        if stripped.lower().startswith("## known issues"):
            current_section = "known_issues"
            continue
        if stripped.startswith("- ") and current_section:
            text = stripped[2:].strip()
            if text.lower() in {"none", "none."}:
                continue
            if current_section == "deviations":
                deviations.append(text)
            else:
                known_issues.append(text)

    return deviations, known_issues


def generate_contract(step_n: int, prev_tag: str | None = None) -> dict[str, Any]:
    prev = prev_tag or (f"step-{step_n - 1:02d}-complete" if step_n > 0 else "HEAD~1")
    created, modified = _git_diff_stat(prev)
    py_files = [entry["path"] for entry in created + modified if entry["path"].endswith(".py")]
    api_surface = _extract_api_surface(py_files)
    configs = _list_new_configs(created)
    deviations, known_issues = _parse_deviations()

    contract = {
        "step": step_n,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "files_created": created,
        "files_modified": modified,
        "public_api": api_surface,
        "configuration_created": configs,
        "deviations": deviations,
        "known_issues": known_issues,
    }
    _validate_contract_entries(contract)
    return contract


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate integration contract for a step")
    parser.add_argument("--step", type=int, required=True)
    parser.add_argument("--prev-tag", type=str, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    contract = generate_contract(args.step, args.prev_tag)

    output_path = args.output or (CONTRACTS_DIR / f"step_{args.step:02d}_contract.json")
    output_path.write_text(json.dumps(contract, indent=2), encoding="utf-8")
    print(f"Contract generated: {output_path}")
    print(f"  Files created: {len(contract['files_created'])}")
    print(f"  Files modified: {len(contract['files_modified'])}")
    print(f"  API modules: {len(contract['public_api'])}")
    print(f"  Deviations: {len(contract['deviations'])}")
    print(f"  Known issues: {len(contract['known_issues'])}")


if __name__ == "__main__":
    main()
