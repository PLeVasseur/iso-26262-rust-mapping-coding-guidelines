from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_fls_grounding  # noqa: E402


def test_run_grounding_validation_writes_report(monkeypatch, tmp_path: Path) -> None:
    guidelines_root = tmp_path / "guidelines"
    heldout_manifest = tmp_path / "heldout.json"
    exemplar_manifest = tmp_path / "exemplars.json"
    output = tmp_path / "report.json"

    monkeypatch.setattr(
        validate_fls_grounding,
        "_artifact_report",
        lambda **kwargs: {
            "label": kwargs["label"],
            "path": str(kwargs["rst_path"]),
            "artifact": {
                "governing_obligation": "x",
                "construct_terms": [],
                "code_tokens": [],
                "supporting_phrases": [],
                "prior_documents": [],
                "prior_sections": [],
                "ambiguity_notes": ["broad_document_priors"],
            },
            "checks": {
                "top_level_fields_exact": True,
                "no_legacy_fields": True,
                "priors_have_only_allowed_keys": True,
                "priors_do_not_expose_candidate_ids": True,
                "weak_evidence_noted": True,
                "semantic_expectations_checked": True,
                "document_family_match": True,
                "section_family_match": True,
            },
            "semantic_expectations": {
                "has_expectations": True,
                "document_family_match": True,
                "section_family_match": True,
            },
        },
    )

    source_path = guidelines_root / "src" / "coding-guidelines" / "x.rst"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("Title\n=====\n", encoding="utf-8")

    heldout_manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "stable_identifier": "heldout::x",
                        "source_path": "src/coding-guidelines/x.rst",
                        "expected_fls_neighborhood_type": {
                            "document_family": "x",
                            "section_family": "x",
                        },
                        "revision_provenance": {
                            "original_expectation": {"document_family": "x", "section_family": "x"},
                            "revised_expectation": {"document_family": "x", "section_family": "x"},
                        },
                    }
                ],
                "status": "revised_post_freeze_ws6_boundary",
                "revision_history": [{"revision_id": "r1"}],
            }
        ),
        encoding="utf-8",
    )
    exemplar_manifest.write_text(
        json.dumps({"exemplars": [{"path": "src/coding-guidelines/x.rst"}]}),
        encoding="utf-8",
    )

    payload = validate_fls_grounding.run_grounding_validation(
        guidelines_repo_root=guidelines_root,
        heldout_manifest_path=heldout_manifest,
        exemplar_manifest_path=exemplar_manifest,
        output_path=output,
    )

    assert payload["summary"]["artifact_count"] == 2
    assert payload["summary"]["all_semantic_expectations_satisfied"] is True
    assert payload["summary"]["heldout_manifest_status"] == "revised_post_freeze_ws6_boundary"
    assert payload["summary"]["heldout_manifest_has_post_freeze_revisions"] is True
    assert output.exists()


def test_semantic_expectations_use_prior_neighborhood_only() -> None:
    artifact = {
        "governing_obligation": "Unsafe extern blocks require explicit unsafe markers.",
        "construct_terms": ["ffi", "extern", "unsafe"],
        "code_tokens": [],
        "supporting_phrases": ["External blocks and unsafe attributes must remain explicit."],
        "prior_documents": [],
        "prior_sections": [],
        "ambiguity_notes": ["broad_document_priors", "broad_section_priors"],
    }

    checks = validate_fls_grounding._semantic_expectation_checks(
        artifact=artifact,
        expected_neighborhood={
            "document_family": "ffi",
            "section_family": "external blocks / unsafe attributes",
        },
    )

    assert checks["document_family_match"] is False
    assert checks["section_family_match"] is False


def test_family_tokens_do_not_apply_alias_or_suffix_laundering() -> None:
    assert validate_fls_grounding._family_tokens("external blocks") == {"external", "blocks"}
    assert validate_fls_grounding._family_tokens("ffi") == {"ffi"}


def test_composite_family_expectation_does_not_pass_on_one_generic_token() -> None:
    artifact = {
        "prior_documents": [{"document_link": "functions.html", "score": 1.0, "evidence": {}}],
        "prior_sections": [
            {
                "section_link": "glossary.html#function-item-type",
                "score": 1.0,
                "evidence": {
                    "document_title_hits": [],
                    "section_title_hits": ["function"],
                    "role_feature_hits": [],
                },
            }
        ],
    }

    checks = validate_fls_grounding._semantic_expectation_checks(
        artifact=artifact,
        expected_neighborhood={
            "document_family": "function / associated item",
            "section_family": "function / call",
        },
    )

    assert checks["document_family_match"] is False
    assert checks["section_family_match"] is False


def test_single_specific_token_expectation_can_match() -> None:
    artifact = {
        "prior_documents": [{"document_link": "ffi.html", "score": 1.0, "evidence": {}}],
        "prior_sections": [
            {
                "section_link": "types-and-traits.html#union-types",
                "score": 1.0,
                "evidence": {
                    "document_title_hits": [],
                    "section_title_hits": ["union"],
                    "role_feature_hits": [],
                },
            }
        ],
    }

    checks = validate_fls_grounding._semantic_expectation_checks(
        artifact=artifact,
        expected_neighborhood={"document_family": "ffi", "section_family": "union"},
    )

    assert checks["document_family_match"] is True
    assert checks["section_family_match"] is True
