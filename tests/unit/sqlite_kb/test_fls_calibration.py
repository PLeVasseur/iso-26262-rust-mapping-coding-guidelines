from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host import fls_calibration  # noqa: E402


def test_evaluate_calibration_items_counts_topk_and_unresolved(monkeypatch) -> None:
    items = [
        {
            "path": "a.rst",
            "packet": {"title": "Unsafe behavior", "construct_terms": ["unsafe"]},
            "acceptable_ids": ["fls_a"],
            "acceptable_chapters": ["Unsafety"],
            "should_abstain": False,
        },
        {
            "path": "b.rst",
            "packet": {"title": "Ambiguous behavior", "construct_terms": ["ambiguous"]},
            "acceptable_ids": ["fls_b"],
            "acceptable_chapters": ["Expressions"],
            "should_abstain": False,
        },
    ]

    def fake_resolver(packet, policy_overrides=None):
        if "Unsafe" in str(packet.get("title", "")):
            return {
                "paragraph_id": "fls_a",
                "chapter": "Unsafety",
                "decision": {
                    "publish_accept": True,
                    "review_candidate": True,
                    "top_candidates": [{"paragraph_id": "fls_a"}],
                    "reason_code": "ACCEPTED",
                },
            }
        return {
            "paragraph_id": "fls_UNRESOLVED",
            "chapter": "",
            "decision": {
                "publish_accept": False,
                "review_candidate": True,
                "top_candidates": [{"paragraph_id": "fls_b"}],
                "reason_code": "LOW_CONFIDENCE_SCORE",
            },
        }

    monkeypatch.setattr(fls_calibration, "resolve_fls_for_guideline", fake_resolver)
    report = fls_calibration.evaluate_calibration_items(items=items)

    assert report["total"] == 2
    assert report["strict_top1"] == 1
    assert report["topk_contains"] == 2
    assert report["unresolved"] == 1
    assert report["false_reject"] == 1


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


def test_threshold_sweep_returns_best_candidate(monkeypatch) -> None:
    items = [{"path": "x.rst", "packet": {}, "acceptable_ids": [], "acceptable_chapters": []}]

    def fake_eval(*, items, policy_overrides=None):
        score = float(
            (policy_overrides or {}).get("thresholds", {}).get("min_confidence_score", 1.0)
        )
        return {
            "strict_top1_ratio": max(0.0, 1.0 - score),
            "topk_ratio": max(0.0, 1.0 - score),
            "unresolved_ratio": score,
            "false_accept": 0,
            "total": len(items),
            "strict_top1": 0,
            "topk_contains": 0,
            "chapter_match": 0,
            "unresolved": 0,
            "false_reject": 0,
            "rows": [],
        }

    monkeypatch.setattr(fls_calibration, "evaluate_calibration_items", fake_eval)
    out = fls_calibration.run_threshold_sweep(items=items, base_policy={"thresholds": {}})

    assert out["candidate_count"] > 0
    assert isinstance(out["best"], dict)
    assert "thresholds" in out["best"]
