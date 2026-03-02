"""Safe import utilities for loading modules from the guidelines repo."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType

GUIDELINES_REPO_ROOT = Path(
    os.environ.get(
        "GUIDELINES_REPO", "/Users/pete.levasseur/personal/safety-critical-rust-coding-guidelines"
    )
)


def import_from_repo(module_name: str, file_path: Path) -> ModuleType:
    """Import a module from an absolute file path without mutating sys.path."""
    if not file_path.exists():
        raise FileNotFoundError(
            f"Cannot import {module_name}: {file_path} does not exist. "
            "Ensure GUIDELINES_REPO is set and checked out."
        )

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {file_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def import_guideline_templates() -> ModuleType:
    return import_from_repo(
        "guideline_templates",
        GUIDELINES_REPO_ROOT / "scripts" / "common" / "guideline_templates.py",
    )


def import_rustdoc_utils() -> ModuleType:
    return import_from_repo(
        "rustdoc_utils",
        GUIDELINES_REPO_ROOT / "scripts" / "rustdoc_utils.py",
    )
