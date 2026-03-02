"""Re-render RST from monolith-persisted artifacts.

This script is Step 2's standalone path: read writer outputs from a completed
run, render corrected RST in parallel output directory, and emit traceability
artifacts for downstream steps.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - direct script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from scripts.rendering_v2.rst_renderer import (
        RendererInput,
        render_guideline_rst,
        serialize_citation_key_map,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script invocation
    from rst_renderer import RendererInput, render_guideline_rst, serialize_citation_key_map


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing required JSONL file: {path}")

    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(json.loads(stripped))
    return rows


def _index_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        draft_id = str(row.get("draft_id", "")).strip()
        target_id = str(row.get("target_id", "")).strip()
        prompt_id = str(row.get("prompt_id", "")).strip()

        for key in (draft_id, target_id, prompt_id):
            if key:
                index.setdefault(key, row)
    return index


def _slug_prompt(prompt_id: str) -> str:
    return prompt_id.strip().lower().replace("_", "-")


def _as_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [segment.strip() for segment in value.split(",") if segment.strip()]
    return []


def _as_mode(value: Any, default: str = "runnable") -> str:
    mode = str(value or "").strip().lower()
    return mode or default


def _derive_citation_keys(bib_rows: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    for row in bib_rows:
        raw = str(row.get("citation_key", "")).strip()
        if raw:
            keys.append(raw)
    return keys


def _collect_bibliography_rows(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    rows = metadata.get("bibliography_rows", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def rerender_from_artifacts(
    run_dir: Path,
    guidelines_repo_root: Path,
    output_subdir: str = "rerendered_rst",
) -> list[dict[str, Any]]:
    drafts = _load_jsonl(run_dir / "drafts.jsonl")
    writer_dir = run_dir / "writer_subagent_outputs"

    evidence_by_key = _index_rows(_load_jsonl(writer_dir / "evidence_synthesizer.jsonl"))
    example_by_key = _index_rows(_load_jsonl(writer_dir / "example_author.jsonl"))
    rationale_by_key = _index_rows(_load_jsonl(writer_dir / "rationale_author.jsonl"))
    metadata_by_key = _index_rows(_load_jsonl(writer_dir / "metadata_citation_curator.jsonl"))
    amplification_by_key = _index_rows(_load_jsonl(writer_dir / "amplification_author.jsonl"))

    out_dir = run_dir / output_subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.rst"):
        stale.unlink()

    results: list[dict[str, Any]] = []
    citation_map_by_guideline: dict[str, dict[str, str]] = {}

    for draft in drafts:
        status = str(draft.get("status", "")).strip().lower()
        if status == "abstain":
            continue

        draft_id = str(draft.get("draft_id", "")).strip()
        target_id = str(draft.get("target_id", "")).strip()
        prompt_id = str(draft.get("target_prompt_id", "")).strip()
        if not prompt_id:
            prompt_id = str(draft.get("prompt_id", "")).strip()
        if not prompt_id:
            continue

        lookup_keys = [draft_id, target_id, prompt_id]

        def _lookup(
            index: dict[str, dict[str, Any]],
            keys: list[str] = lookup_keys,
        ) -> dict[str, Any]:
            for key in keys:
                if key and key in index:
                    return index[key]
            return {}

        _ = _lookup(evidence_by_key)
        example = _lookup(example_by_key)
        rationale = _lookup(rationale_by_key)
        metadata = _lookup(metadata_by_key)
        amplification = _lookup(amplification_by_key)

        bibliography_rows = _collect_bibliography_rows(metadata)
        citation_keys = _derive_citation_keys(bibliography_rows)

        renderer_input = RendererInput(
            title=str(metadata.get("title", draft.get("title", f"Guideline for {prompt_id}"))),
            guideline_text=str(
                amplification.get("guideline_amplification_text", draft.get("guideline", ""))
            ),
            rationale_text=str(rationale.get("rationale_text", draft.get("rationale", ""))),
            non_compliant_narrative=str(
                example.get(
                    "non_compliant_narrative",
                    "Non-compliant example demonstrates the hazard trigger.",
                )
            ),
            non_compliant_code=str(example.get("non_compliant_code", "fn main() {}")),
            compliant_narrative=str(
                example.get(
                    "compliant_narrative",
                    "Compliant example demonstrates mitigation.",
                )
            ),
            compliant_code=str(example.get("compliant_code", "fn main() {}")),
            bibliography_rows=bibliography_rows,
            non_compliant_mode=_as_mode(draft.get("example_execution_mode"), default="runnable"),
            compliant_mode=_as_mode(example.get("compliant_mode"), default="runnable"),
            non_compliant_miri_intent=str(example.get("non_compliant_miri_intent", "none")).strip(),
            compliant_miri_intent=str(example.get("compliant_miri_intent", "none")).strip(),
            category=str(draft.get("category", "advisory")),
            normative_strength=str(
                amplification.get("normative_strength", draft.get("strength", "should"))
            ),
            decidability=str(metadata.get("decidability", "undecidable")),
            scope=str(metadata.get("scope", "system")),
            tags=_as_tags(metadata.get("tags", draft.get("tags", []))),
            citation_keys_used=citation_keys,
            prompt_id=prompt_id,
            exemplar_ids_used=[
                str(item).strip()
                for item in draft.get("exemplar_ids_used", [])
                if str(item).strip()
            ],
        )

        artifacts = render_guideline_rst(renderer_input, guidelines_repo_root)
        file_name = f"{_slug_prompt(prompt_id)}.rst"
        output_path = out_dir / file_name
        output_path.write_text(artifacts.rst, encoding="utf-8")

        citation_map_by_guideline[artifacts.guideline_id] = artifacts.citation_key_map
        results.append(
            {
                "draft_id": draft_id,
                "prompt_id": prompt_id,
                "file": file_name,
                "output_path": str(output_path),
                "guideline_id": artifacts.guideline_id,
                "target_id": target_id,
            }
        )

    rerender_manifest_path = out_dir / "rerender_manifest.json"
    rerender_manifest_path.write_text(
        json.dumps(
            {
                "source_run_dir": str(run_dir),
                "output_dir": str(out_dir),
                "results": results,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    serialize_citation_key_map(citation_map_by_guideline, out_dir / "citation_key_map.json")

    guideline_manifest = {
        "run_id": run_dir.name,
        "guidelines": [
            {
                "prompt_id": row["prompt_id"],
                "target_id": row.get("target_id", ""),
                "guideline_id": row.get("guideline_id", ""),
                "file_path": row["output_path"],
                "build_status": "pending",
            }
            for row in results
        ],
    }
    (out_dir / "guideline_manifest.json").write_text(
        json.dumps(guideline_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-render RST from persisted artifacts")
    parser.add_argument("--run-dir", required=True, help="Path to completed run directory")
    parser.add_argument(
        "--guidelines-repo",
        required=True,
        help="Path to upstream safety-critical-rust-coding-guidelines checkout",
    )
    parser.add_argument(
        "--output-subdir",
        default="rerendered_rst",
        help="Subdirectory under run dir for rendered outputs",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    run_dir = Path(args.run_dir).expanduser().resolve()
    guidelines_repo = Path(args.guidelines_repo).expanduser().resolve()
    results = rerender_from_artifacts(
        run_dir=run_dir,
        guidelines_repo_root=guidelines_repo,
        output_subdir=args.output_subdir,
    )
    print(json.dumps({"rendered_count": len(results), "run_dir": str(run_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
