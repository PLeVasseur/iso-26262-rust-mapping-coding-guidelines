from __future__ import annotations

from pathlib import Path

GLOB_BLOCK = [
    ".. toctree::",
    "   :maxdepth: 1",
    "   :titlesonly:",
    "   :glob:",
    "",
    "   gui_*",
]


def _has_gui_glob(lines: list[str]) -> bool:
    in_toctree = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(".. toctree::"):
            in_toctree = True
            continue
        if in_toctree:
            if not stripped or stripped.startswith(":"):
                continue
            if line.startswith(" "):
                if stripped == "gui_*" or stripped.endswith("/gui_*"):
                    return True
                continue
            in_toctree = False
    return False


def ensure_glob_toctree(index_path: Path) -> bool:
    """Ensure chapter index contains a gui_* glob toctree.

    Returns True when the file was modified.
    """

    if index_path.exists():
        lines = index_path.read_text(encoding="utf-8").splitlines()
    else:
        title = index_path.parent.name.replace("-", " ").title()
        lines = [title, "=" * len(title), ""]

    if _has_gui_glob(lines):
        return False

    new_lines = list(lines)
    if new_lines and new_lines[-1].strip():
        new_lines.append("")
    new_lines.extend(GLOB_BLOCK)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    return True
