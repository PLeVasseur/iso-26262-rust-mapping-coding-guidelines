from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.reviewer_admissibility import (  # noqa: E402
    evaluate_review_admissibility,
    write_review_admissibility_artifacts,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _seed_run(root: Path) -> tuple[Path, Path]:
    run_dir = root / "run"
    publish_root = root / ".cache" / "sqlite_kb" / "reports" / "writer_publish" / run_dir.name
    _write_json(run_dir / "writer_quality_gate_report.json", {"status": "pass"})
    _write_json(run_dir / "writer_conformance_report.json", {"status": "pass"})
    draft = {
        "target_id": "RET-ISSUE-006",
        "draft_id": "draft::RET-ISSUE-006::atom::must-use",
        "atom_id": "RET-ISSUE-006::atom::must-use",
        "title": "Require must_use diagnostics for non-ignorable values",
        "chapter": "attributes",
        "construct_terms": ["must_use", "lint"],
        "claim_to_evidence_map": [{"claim_id": "c1", "claim_text": "must_use should fail review"}],
    }
    _write_jsonl(run_dir / "drafts.jsonl", [draft])
    output_rows = [
        {
            "draft_id": draft["draft_id"],
            "output": {
                "guideline_amplification_text": "Non-ignorable returned values shall trigger must_use diagnostics.",
                "rationale_text": "Ignored safety-relevant results are easy to miss.",
                "non_compliant_narrative": "The result is ignored.",
                "non_compliant_code": "let _ = compute();",
                "compliant_narrative": "The result is annotated with must_use.",
                "compliant_code": "#[must_use]\nfn compute() -> Result<(), ()> { Ok(()) }",
                "tags": ["lint", "must_use"],
                "bibliography_rows": [
                    {
                        "id": "bib1",
                        "url": "https://doc.rust-lang.org/reference/attributes/diagnostics.html#the-must_use-attribute",
                    }
                ],
                "editorial_metadata": {
                    "candidate_chapter": "attributes",
                    "primary_construct_family": "diagnostics",
                },
            },
        }
    ]
    _write_jsonl(run_dir / "writer_subagent_outputs" / "amplification_author.jsonl", output_rows)
    _write_jsonl(run_dir / "writer_subagent_outputs" / "rationale_author.jsonl", output_rows)
    _write_jsonl(run_dir / "writer_subagent_outputs" / "example_author.jsonl", output_rows)
    _write_jsonl(
        run_dir / "writer_subagent_outputs" / "metadata_citation_curator.jsonl", output_rows
    )
    _write_json(
        publish_root / "internal_rendered_candidate_manifest.json",
        {
            "run_id": run_dir.name,
            "internal_render_root": str(publish_root / "exported_guidelines"),
            "rendered_candidates": [
                {
                    "draft_id": draft["draft_id"],
                    "atom_id": draft["atom_id"],
                    "target_id": draft["target_id"],
                    "rendered_path": "attributes/gui_demo.rst",
                    "chapter": "attributes",
                    "title": draft["title"],
                    "admissibility_status": "",
                }
            ],
            "unrendered_candidates": [],
            "notes": "",
        },
    )
    exported = publish_root / "exported_guidelines"
    exported.mkdir(parents=True, exist_ok=True)
    (exported / "index.rst").write_text(".. toctree::\n\n   attributes/index\n", encoding="utf-8")
    return run_dir, publish_root


def test_review_admissibility_passes_strong_candidate(tmp_path: Path, monkeypatch) -> None:
    run_dir, _ = _seed_run(tmp_path)
    monkeypatch.setattr(
        "retrieval.writer_host.publish_mapping.gather_candidates",
        lambda packet: ([{"paragraph_id": "fls_safe001"}], []),
    )
    monkeypatch.setattr(
        "retrieval.writer_host.publish_mapping.resolve_fls_for_guideline",
        lambda packet, precomputed_candidates=None, precomputed_variants=None: {
            "paragraph_id": "fls_safe001",
            "decision": {"accepted": True, "reason_code": "ACCEPTED"},
        },
    )
    monkeypatch.setattr(
        "retrieval.writer_host.publish_mapping.validate_fls_id",
        lambda value: value == "fls_safe001",
    )

    report = evaluate_review_admissibility(root=tmp_path, run_dir=run_dir)

    assert report["status"] == "pass"
    assert report["counts_by_admissibility_status"]["admit"] == 1
    assert report["entries"][0]["rendered_candidate_path"] == "attributes/gui_demo.rst"
    paths = write_review_admissibility_artifacts(run_dir=run_dir, report=report)
    assert Path(paths["admissibility"]).exists()
    assert Path(paths["metadata"]).exists()


def test_review_admissibility_marks_duplicate_provenance_merge_required(
    tmp_path: Path, monkeypatch
) -> None:
    run_dir, publish_root = _seed_run(tmp_path)
    draft_rows = [
        {
            "target_id": "RET-RESOLVE-004",
            "draft_id": "draft::RET-RESOLVE-004::atom::prov-a",
            "atom_id": "RET-RESOLVE-004::atom::prov-a",
            "title": "Prefer Strict Provenance pointer APIs",
            "chapter": "unsafety",
            "construct_terms": ["provenance", "pointer"],
            "claim_to_evidence_map": [{"claim_id": "c1", "claim_text": "claim"}],
        },
        {
            "target_id": "RET-RESOLVE-005",
            "draft_id": "draft::RET-RESOLVE-005::atom::prov-b",
            "atom_id": "RET-RESOLVE-005::atom::prov-b",
            "title": "Prefer Strict Provenance over exposed provenance",
            "chapter": "unsafety",
            "construct_terms": ["strict provenance", "pointer"],
            "claim_to_evidence_map": [{"claim_id": "c2", "claim_text": "claim"}],
        },
    ]
    _write_jsonl(run_dir / "drafts.jsonl", draft_rows)
    rows = []
    for draft in draft_rows:
        rows.append(
            {
                "draft_id": draft["draft_id"],
                "output": {
                    "guideline_amplification_text": draft["title"] + " shall be preferred.",
                    "rationale_text": "why",
                    "non_compliant_narrative": "bad",
                    "non_compliant_code": "bad();",
                    "compliant_narrative": "good",
                    "compliant_code": "good();",
                    "tags": ["unsafe", "provenance"],
                    "bibliography_rows": [],
                    "editorial_metadata": {
                        "candidate_chapter": "unsafety",
                        "primary_construct_family": "provenance",
                    },
                },
            }
        )
    for name in (
        "amplification_author.jsonl",
        "rationale_author.jsonl",
        "example_author.jsonl",
        "metadata_citation_curator.jsonl",
    ):
        _write_jsonl(run_dir / "writer_subagent_outputs" / name, rows)
    _write_json(
        publish_root / "internal_rendered_candidate_manifest.json",
        {
            "run_id": run_dir.name,
            "internal_render_root": str(publish_root / "exported_guidelines"),
            "rendered_candidates": [
                {
                    "draft_id": draft_rows[0]["draft_id"],
                    "atom_id": draft_rows[0]["atom_id"],
                    "target_id": draft_rows[0]["target_id"],
                    "rendered_path": "unsafety/gui_a.rst",
                    "chapter": "unsafety",
                    "title": draft_rows[0]["title"],
                    "admissibility_status": "",
                },
                {
                    "draft_id": draft_rows[1]["draft_id"],
                    "atom_id": draft_rows[1]["atom_id"],
                    "target_id": draft_rows[1]["target_id"],
                    "rendered_path": "unsafety/gui_b.rst",
                    "chapter": "unsafety",
                    "title": draft_rows[1]["title"],
                    "admissibility_status": "",
                },
            ],
            "unrendered_candidates": [],
            "notes": "",
        },
    )
    (publish_root / "exported_guidelines" / "index.rst").write_text(
        ".. toctree::\n\n   unsafety/index\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "retrieval.writer_host.publish_mapping.gather_candidates",
        lambda packet: ([{"paragraph_id": "fls_safe001"}], []),
    )
    monkeypatch.setattr(
        "retrieval.writer_host.publish_mapping.resolve_fls_for_guideline",
        lambda packet, precomputed_candidates=None, precomputed_variants=None: {
            "paragraph_id": "fls_safe001",
            "decision": {"accepted": True, "reason_code": "ACCEPTED"},
        },
    )
    monkeypatch.setattr(
        "retrieval.writer_host.publish_mapping.validate_fls_id",
        lambda value: value == "fls_safe001",
    )

    report = evaluate_review_admissibility(root=tmp_path, run_dir=run_dir)

    statuses = {entry["draft_id"]: entry["admissibility_status"] for entry in report["entries"]}
    assert "merge_required" in statuses.values()
    assert report["family_resolution_report"]["clusters"][0]["cluster_id"] == "strict_provenance"


def test_review_admissibility_keeps_scoped_extern_abi_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    run_dir, publish_root = _seed_run(tmp_path)
    draft = {
        "target_id": "RET-ISSUE-009",
        "draft_id": "draft::RET-ISSUE-009::atom::explicit-extern-abi",
        "atom_id": "RET-ISSUE-009::atom::explicit-extern-abi",
        "title": "Spell the ABI explicitly on every extern block",
        "chapter": "functions",
        "construct_terms": [
            'extern "ABI" blocks',
            "core::cmp::PartialEq",
            "core::ptr strict provenance APIs",
        ],
        "claim_to_evidence_map": [{"claim_id": "c1", "claim_text": "claim"}],
    }
    _write_jsonl(run_dir / "drafts.jsonl", [draft])
    row = {
        "draft_id": draft["draft_id"],
        "output": {
            "guideline_amplification_text": (
                "Every `extern` block shall spell its ABI explicitly. "
                "Code shall not rely on the default ABI for an `extern` block."
            ),
            "rationale_text": "why",
            "non_compliant_narrative": "bad",
            "non_compliant_code": "bad();",
            "compliant_narrative": "good",
            "compliant_code": "good();",
            "tags": ["functions", "ffi", "extern-blocks", "abi", "style-consistency"],
            "bibliography_rows": [],
            "editorial_metadata": {
                "candidate_chapter": "functions",
                "primary_construct_family": 'extern "ABI" blocks',
            },
            "fls_candidate": {
                "chapter": "functions",
                "construct_family": 'extern "ABI" blocks',
            },
        },
    }
    for name in (
        "amplification_author.jsonl",
        "rationale_author.jsonl",
        "example_author.jsonl",
        "metadata_citation_curator.jsonl",
    ):
        _write_jsonl(run_dir / "writer_subagent_outputs" / name, [row])
    _write_json(
        publish_root / "internal_rendered_candidate_manifest.json",
        {
            "run_id": run_dir.name,
            "internal_render_root": str(publish_root / "exported_guidelines"),
            "rendered_candidates": [
                {
                    "draft_id": draft["draft_id"],
                    "atom_id": draft["atom_id"],
                    "target_id": draft["target_id"],
                    "rendered_path": "functions/gui_abi.rst",
                    "chapter": "functions",
                    "title": draft["title"],
                    "admissibility_status": "",
                }
            ],
            "unrendered_candidates": [],
            "notes": "",
        },
    )
    (publish_root / "exported_guidelines" / "index.rst").write_text(
        ".. toctree::\n\n   functions/index\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "retrieval.writer_host.publish_mapping.gather_candidates",
        lambda packet: ([{"paragraph_id": "fls_safe001"}], []),
    )
    monkeypatch.setattr(
        "retrieval.writer_host.publish_mapping.resolve_fls_for_guideline",
        lambda packet, precomputed_candidates=None, precomputed_variants=None: {
            "paragraph_id": "fls_safe001",
            "decision": {"accepted": True, "reason_code": "ACCEPTED"},
        },
    )
    monkeypatch.setattr(
        "retrieval.writer_host.publish_mapping.validate_fls_id",
        lambda value: value == "fls_safe001",
    )

    report = evaluate_review_admissibility(root=tmp_path, run_dir=run_dir)

    entry = report["entries"][0]
    assert entry["guideline_family_key"] == "extern_abi"
    assert entry["normalized_taxonomy_chapter"] == "functions"
    assert entry["admissibility_status"] == "admit"
