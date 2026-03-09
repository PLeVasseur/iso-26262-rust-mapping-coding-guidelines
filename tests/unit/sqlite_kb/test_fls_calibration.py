from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host import fls_calibration  # noqa: E402


def test_evaluate_calibration_items_reports_grounding_only_runtime(monkeypatch) -> None:
    items = [
        {
            "path": "a.rst",
            "packet": {
                "governing_obligation": "Unsafe behavior",
                "construct_terms": ["unsafe"],
            },
            "acceptable_ids": ["fls_a"],
            "acceptable_chapters": ["Unsafety"],
        },
        {
            "path": "b.rst",
            "packet": {
                "governing_obligation": "Ambiguous behavior",
                "construct_terms": ["ambiguous"],
            },
            "acceptable_ids": ["fls_b"],
            "acceptable_chapters": ["Expressions"],
        },
    ]

    def fake_resolver(packet, policy_overrides=None):
        del policy_overrides
        if "Unsafe" in str(packet.get("governing_obligation", "")):
            return {
                "paragraph_id": "fls_UNRESOLVED",
                "decision": {
                    "publish_accept": False,
                    "review_candidate": False,
                    "top_candidates": [],
                    "reason_code": "WS7_REQUIRED",
                    "grounding_only_runtime": True,
                },
            }
        return {
            "paragraph_id": "fls_UNRESOLVED",
            "decision": {
                "publish_accept": False,
                "review_candidate": False,
                "top_candidates": [],
                "reason_code": "WS7_REQUIRED",
                "grounding_only_runtime": True,
            },
        }

    monkeypatch.setattr(fls_calibration, "resolve_fls_for_guideline", fake_resolver)
    report = fls_calibration.evaluate_calibration_items(items=items)

    assert report["total"] == 2
    assert report["ws7_required"] == 2
    assert report["grounding_only_runtime"] == 2
    assert report["abstention_correct"] == 2
    assert report["publish_accept_violations"] == 0


def test_load_calibration_items_prefers_dataset_over_manifest(tmp_path: Path) -> None:
    guidelines_root = tmp_path / "guidelines"
    guidelines_root.mkdir(parents=True)
    rst = guidelines_root / "x.rst"
    rst.write_text("Title\n=====\n\n:tags: unsafe\n", encoding="utf-8")

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"exemplars": []}), encoding="utf-8")

    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "path": "x.rst",
                        "acceptable_ids": ["fls_x"],
                        "acceptable_chapters": ["Unsafety"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    items = fls_calibration.load_calibration_items(
        manifest_path=manifest,
        guidelines_repo_root=guidelines_root,
        dataset_path=dataset,
    )

    assert len(items) == 1
    assert items[0]["acceptable_ids"] == ["fls_x"]


def test_threshold_sweep_is_disabled_until_ws7() -> None:
    with pytest.raises(RuntimeError, match="WS7_REQUIRED"):
        fls_calibration.run_threshold_sweep(items=[], base_policy={})


def test_load_calibration_items_rejects_runtime_use_prohibited_dataset(tmp_path: Path) -> None:
    guidelines_root = tmp_path / "guidelines"
    guidelines_root.mkdir(parents=True)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"exemplars": []}), encoding="utf-8")
    dataset = tmp_path / "heldout.json"
    dataset.write_text(
        json.dumps({"runtime_use_prohibited": True, "items": []}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="runtime_use_prohibited"):
        fls_calibration.load_calibration_items(
            manifest_path=manifest,
            guidelines_repo_root=guidelines_root,
            dataset_path=dataset,
        )


def test_build_resolution_packet_from_rst_filters_low_value_seed_terms(tmp_path: Path) -> None:
    rst = tmp_path / "sample.rst"
    rst.write_text(
        """
Use of unsafe pointers in handlers
=================================

This guideline explains pointer validity in handlers.

|

.. rust-example::

   unsafe { *ptr }
""".strip()
        + "\n",
        encoding="utf-8",
    )

    packet = fls_calibration.build_resolution_packet_from_rst(rst)

    assert "of" not in packet["construct_terms"]
    assert "in" not in packet["construct_terms"]
    assert all(phrase != "|" for phrase in packet["supporting_phrases"])


def test_build_resolution_packet_from_rst_keeps_code_tokens_code_only(tmp_path: Path) -> None:
    rst = tmp_path / "sample.rst"
    rst.write_text(
        """
Unsafe extern declarations
==========================

Unsafe extern blocks require explicit markers.

.. rust-example::
   :caption: Non-compliant example

   extern "C" {
       fn malloc(size: usize) -> *mut c_void;
   }

This prose must not leak into code tokens.

.. rust-example::
   :caption: Compliant example

   unsafe extern "C" {
       fn malloc(size: usize) -> *mut c_void;
   }
""".strip()
        + "\n",
        encoding="utf-8",
    )

    packet = fls_calibration.build_resolution_packet_from_rst(rst)

    assert "extern" in packet["code_tokens"]
    assert "malloc" in packet["code_tokens"]
    assert "caption" not in packet["code_tokens"]
    assert "compliant" not in packet["code_tokens"]
    assert "prose" not in packet["code_tokens"]


def test_build_resolution_packet_from_rst_cleans_supporting_phrase_formatting(
    tmp_path: Path,
) -> None:
    rst = tmp_path / "sample.rst"
    rst.write_text(
        """
Unsafe macro handling
=====================

| **Debugging Complexity** - Errors point to expanded code rather than source locations.
| - Macros may inhibit compiler optimizations.

.. rust-example::

   unsafe { macro_call!() }
""".strip()
        + "\n",
        encoding="utf-8",
    )

    packet = fls_calibration.build_resolution_packet_from_rst(rst)

    assert all(not phrase.startswith("|") for phrase in packet["supporting_phrases"])
    assert all("**" not in phrase for phrase in packet["supporting_phrases"])


def test_build_resolution_packet_from_rst_aggregates_later_legal_code_blocks(
    tmp_path: Path,
) -> None:
    rst = tmp_path / "sample.rst"
    rst.write_text(
        """
Unsafe visibility in unsafe code
===============================

Unsafe visibility matters.

.. rust-example::

   #[unsafe(no_mangle)]
   fn convert() {}

.. rust-example::

   unsafe extern "C" {
       fn malloc(size: usize);
   }
""".strip()
        + "\n",
        encoding="utf-8",
    )

    packet = fls_calibration.build_resolution_packet_from_rst(rst)

    assert "no_mangle" in packet["code_tokens"]
    assert "extern" in packet["code_tokens"]
    assert "malloc" in packet["code_tokens"]


def test_evaluate_calibration_items_is_grounding_runtime_report(monkeypatch) -> None:
    monkeypatch.setattr(
        fls_calibration,
        "resolve_fls_for_guideline",
        lambda packet, policy_overrides=None: {
            "paragraph_id": "fls_UNRESOLVED",
            "decision": {
                "reason_code": "WS7_REQUIRED",
                "grounding_only_runtime": True,
                "publish_accept": False,
                "top_candidates": [],
            },
        },
    )

    report = fls_calibration.evaluate_calibration_items(
        items=[
            {
                "path": "x.rst",
                "packet": {
                    "governing_obligation": "x",
                    "construct_terms": ["unsafe"],
                    "code_tokens": [],
                    "supporting_phrases": [],
                    "prior_documents": [],
                    "prior_sections": [],
                    "ambiguity_notes": [],
                },
                "acceptable_ids": ["fls_x"],
                "acceptable_chapters": ["Unsafety"],
            }
        ]
    )

    assert "strict_top1" not in report
    assert "topk_contains" not in report
    assert report["ws7_required"] == 1


def test_evaluate_calibration_items_has_no_ranking_threshold_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        fls_calibration,
        "resolve_fls_for_guideline",
        lambda packet, policy_overrides=None: {
            "paragraph_id": "fls_UNRESOLVED",
            "decision": {
                "reason_code": "WS7_REQUIRED",
                "grounding_only_runtime": True,
                "publish_accept": False,
                "top_candidates": [],
            },
        },
    )

    report = fls_calibration.evaluate_calibration_items(
        items=[
            {
                "path": "x.rst",
                "packet": {
                    "governing_obligation": "x",
                    "construct_terms": ["unsafe"],
                    "code_tokens": [],
                    "supporting_phrases": [],
                    "prior_documents": [],
                    "prior_sections": [],
                    "ambiguity_notes": [],
                },
                "acceptable_ids": ["fls_x"],
                "acceptable_chapters": ["Unsafety"],
            }
        ]
    )

    assert "min_confidence_score" not in report
    assert "weights" not in report
    assert report["grounding_only_runtime"] == 1
