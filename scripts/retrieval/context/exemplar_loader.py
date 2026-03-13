from __future__ import annotations

from pathlib import Path
from typing import Any

from context.exemplars import get_exemplar_paths


def load_exemplar_bundle(guidelines_repo_root: Path) -> dict[str, Any]:
    paths = get_exemplar_paths(guidelines_repo_root=guidelines_repo_root)
    return {
        "exemplar_ids": [path.stem for path in paths],
        "exemplar_paths": paths,
    }
