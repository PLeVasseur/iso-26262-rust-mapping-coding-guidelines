"""Create exemplar manifest with SHA-256 checksums."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path

GUIDELINES_REPO = Path(
    os.environ.get(
        "GUIDELINES_REPO", "/Users/pete.levasseur/personal/safety-critical-rust-coding-guidelines"
    )
)


def _curated_ids_from_s0_service() -> list[str]:
    source_path = Path("scripts/retrieval/services/s0_phase_a_impl.py")
    if not source_path.exists():
        return []

    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "curated_ids":
                if isinstance(node.value, ast.List):
                    ids: list[str] = []
                    for element in node.value.elts:
                        if isinstance(element, ast.Constant) and isinstance(element.value, str):
                            ids.append(element.value)
                    return ids
    return []


def get_exemplar_paths() -> list[str]:
    curated_ids = _curated_ids_from_s0_service()
    if curated_ids:
        paths: list[str] = []
        for guideline_id in curated_ids:
            matches = sorted(GUIDELINES_REPO.glob(f"src/coding-guidelines/**/{guideline_id}.rst"))
            if matches:
                paths.append(str(matches[0].relative_to(GUIDELINES_REPO)))
        return paths

    source_dir = GUIDELINES_REPO / "src"
    if source_dir.exists():
        fallback = sorted(source_dir.rglob("*.rst"))
        print(f"WARNING: Falling back to first 14 RST files from repo ({len(fallback)} available).")
        return [str(path.relative_to(GUIDELINES_REPO)) for path in fallback[:14]]

    return []


def create_manifest() -> None:
    paths = get_exemplar_paths()
    if not paths:
        raise RuntimeError("Could not determine exemplar file list")

    manifest: dict[str, list[dict[str, str]]] = {"exemplars": []}
    for rel_path in paths:
        full_path = GUIDELINES_REPO / rel_path
        if not full_path.exists():
            print(f"WARNING: Exemplar not found: {rel_path}")
            continue

        digest = hashlib.sha256(full_path.read_bytes()).hexdigest()
        manifest["exemplars"].append({"path": rel_path, "sha256": digest})

    output = Path("data/exemplar_manifest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Exemplar manifest created: {len(manifest['exemplars'])} files -> {output}")


if __name__ == "__main__":
    create_manifest()
