from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.import_utils import GUIDELINES_REPO_ROOT
from scripts.retrieval.rendering.rerender_from_artifacts import rerender_from_artifacts
from scripts.retrieval.rendering.rst_renderer import RendererInput, render_guideline_rst


def _sample_input() -> RendererInput:
    return RendererInput(
        title="Guideline for TEST-001",
        guideline_text="Use auditable patterns in safety-critical code.",
        rationale_text="The rationale explains hazard and mitigation.",
        non_compliant_narrative="This is a non-compliant case.",
        non_compliant_code="unsafe fn bad() {}",
        compliant_narrative="This is a compliant case.",
        compliant_code="fn good() {}",
        bibliography_rows=[
            {
                "citation_key": "RUST-REF-ONE",
                "title": "Rust Reference One",
                "url": "https://doc.rust-lang.org/reference/",
            },
            {
                "citation_key": "already:SCOPED",
                "title": "Missing URL row",
                "url": "",
            },
        ],
        non_compliant_mode="runnable",
        compliant_mode="runnable",
        non_compliant_miri_intent="none",
        compliant_miri_intent="none",
        category="required",
        normative_strength="shall",
        decidability="decidable",
        scope="module",
        tags=["safety", "rust"],
        citation_keys_used=["RUST-REF-ONE", "already:SCOPED"],
        prompt_id="TEST-001",
        exemplar_ids_used=[],
    )


def test_render_guideline_rst_deterministic_and_conformant_shape() -> None:
    first = render_guideline_rst(_sample_input(), GUIDELINES_REPO_ROOT)
    second = render_guideline_rst(_sample_input(), GUIDELINES_REPO_ROOT)

    assert first.guideline_id == second.guideline_id
    assert first.rationale_id == second.rationale_id
    assert re.search(r":id:\s+gui_[A-Za-z0-9]{12}", first.rst)
    assert re.search(r":id:\s+rat_[A-Za-z0-9]{12}", first.rst)
    assert ".. default-domain:: coding-guidelines" in first.rst
    assert ":release: latest" in first.rst
    assert "non_compl_ex_" in first.rst
    assert "compl_ex_" in first.rst
    assert "bib_" in first.rst
    assert first.rst.count(".. rust-example::") == 2
    assert first.rst.count(":edition: 2021") == 2
    assert ":miri:" in first.rst
    assert "evidence_bundle/" not in first.rst
    assert "URL_UNRESOLVED" in first.rst

    assert first.citation_key_map["RUST-REF-ONE"].startswith(f"{first.guideline_id}:")
    assert first.citation_key_map["already:SCOPED"].startswith(f"{first.guideline_id}:")


def test_rerender_from_artifacts_writes_expected_outputs(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    sub = run_dir / "writer_subagent_outputs"
    sub.mkdir(parents=True)

    (run_dir / "drafts.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "draft_id": "draft::test-001",
                        "target_id": "target-001",
                        "target_prompt_id": "TEST-001",
                        "title": "Guideline for TEST-001",
                        "guideline": "Body text.",
                        "rationale": "Rationale text.",
                        "strength": "shall",
                        "category": "required",
                        "status": "draft",
                    }
                ),
                json.dumps(
                    {
                        "draft_id": "draft::abstain",
                        "target_id": "target-abstain",
                        "target_prompt_id": "ABSTAIN-001",
                        "status": "abstain",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    (sub / "evidence_synthesizer.jsonl").write_text(
        json.dumps({"target_id": "target-001", "draft_id": "draft::test-001"}) + "\n",
        encoding="utf-8",
    )
    (sub / "example_author.jsonl").write_text(
        json.dumps(
            {
                "target_id": "target-001",
                "non_compliant_narrative": "bad",
                "non_compliant_code": "unsafe fn bad() {}",
                "compliant_narrative": "good",
                "compliant_code": "fn good() {}",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (sub / "rationale_author.jsonl").write_text(
        json.dumps({"target_id": "target-001", "rationale_text": "Rationale text."}) + "\n",
        encoding="utf-8",
    )
    (sub / "metadata_citation_curator.jsonl").write_text(
        json.dumps(
            {
                "target_id": "target-001",
                "tags": ["safety"],
                "bibliography_rows": [
                    {
                        "citation_key": "C1",
                        "title": "Reference",
                        "url": "https://example.com/spec",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (sub / "amplification_author.jsonl").write_text(
        json.dumps(
            {
                "target_id": "target-001",
                "draft_id": "draft::test-001",
                "prompt_id": "TEST-001",
                "guideline_amplification_text": "Body text.",
                "normative_strength": "shall",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    results = rerender_from_artifacts(run_dir, GUIDELINES_REPO_ROOT)

    assert len(results) == 1
    out = run_dir / "rerendered_rst"
    assert (out / "test-001.rst").exists()

    manifest = json.loads((out / "rerender_manifest.json").read_text(encoding="utf-8"))
    assert manifest["results"][0]["draft_id"] == "draft::test-001"
    assert manifest["results"][0]["prompt_id"] == "TEST-001"
    assert manifest["results"][0]["file"] == "test-001.rst"
    assert str(out / "test-001.rst") == manifest["results"][0]["output_path"]

    citation_map = json.loads((out / "citation_key_map.json").read_text(encoding="utf-8"))
    assert citation_map["citation_placement_policy"] == "renderer_injected"
