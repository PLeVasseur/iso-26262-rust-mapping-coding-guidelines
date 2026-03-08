from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host import publish_mapping  # noqa: E402


def _row_fixture() -> dict[str, object]:
    return {
        "draft": {
            "target_id": "RET-ISSUE-001",
            "title": "Preserve safety invariants on error paths",
            "chapter": "exceptions-and-errors",
            "construct_terms": ["unsafe", "Result"],
            "claim_to_evidence_map": [
                {
                    "claim_id": "RET-ISSUE-001::claim::1",
                    "claim_text": "Unsafe fallback can violate invariants",
                }
            ],
        },
        "amplification": {"guideline_amplification_text": "Preserve safety invariants on errors."},
        "rationale": {"rationale_text": "Weak fault handling can expose UB paths."},
        "examples": {
            "non_compliant_narrative": "Logs and continues after invalid input.",
            "non_compliant_code": "unsafe { *values.get_unchecked(idx) }",
            "compliant_narrative": "Return error before risky operation.",
            "compliant_code": "values.get(idx)",
        },
        "metadata": {
            "tags": ["unsafe", "error-handling"],
            "fls_candidate": {
                "statement": "Unsafe error paths can break invariants",
                "category": "safety required",
            },
        },
    }


def test_map_publish_record_raises_on_unresolved_fls(monkeypatch) -> None:
    monkeypatch.setattr(
        publish_mapping,
        "gather_candidates",
        lambda packet: ([], []),
    )
    monkeypatch.setattr(
        publish_mapping,
        "resolve_fls_for_guideline",
        lambda packet, precomputed_candidates=None, precomputed_variants=None: {
            "paragraph_id": "fls_UNRESOLVED",
            "unresolved_reason": "top candidate confidence score below threshold",
            "decision": {"reason_code": "LOW_CONFIDENCE_SCORE"},
        },
    )

    with pytest.raises(RuntimeError, match="LOW_CONFIDENCE_SCORE|below threshold"):
        publish_mapping.map_publish_record(_row_fixture())


def test_map_publish_record_attaches_fls_resolution_decision(monkeypatch) -> None:
    monkeypatch.setattr(
        publish_mapping,
        "gather_candidates",
        lambda packet: (
            [{"paragraph_id": "fls_unsafe003"}],
            [{"name": "title_focus", "query": "unsafe"}],
        ),
    )
    monkeypatch.setattr(
        publish_mapping,
        "resolve_fls_for_guideline",
        lambda packet, precomputed_candidates=None, precomputed_variants=None: {
            "paragraph_id": "fls_unsafe003",
            "decision": {
                "accepted": True,
                "reason_code": "ACCEPTED",
                "top_score": 0.91,
                "margin": 0.19,
            },
        },
    )
    monkeypatch.setattr(publish_mapping, "validate_fls_id", lambda value: value == "fls_unsafe003")

    mapped = publish_mapping.map_publish_record(_row_fixture())

    assert mapped["fls_id"] == "fls_unsafe003"
    assert mapped["fls_resolution"]["reason_code"] == "ACCEPTED"
    assert mapped["fls_resolution_report"] in (None, "")


def test_map_publish_record_passes_multifield_packet(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_gather(packet):
        seen["packet"] = packet
        return [], []

    monkeypatch.setattr(publish_mapping, "gather_candidates", fake_gather)
    monkeypatch.setattr(
        publish_mapping,
        "resolve_fls_for_guideline",
        lambda packet, precomputed_candidates=None, precomputed_variants=None: {
            "paragraph_id": "fls_unsafe003",
            "decision": {"reason_code": "ACCEPTED", "accepted": True},
        },
    )
    monkeypatch.setattr(publish_mapping, "validate_fls_id", lambda value: value == "fls_unsafe003")

    publish_mapping.map_publish_record(_row_fixture())

    packet = seen.get("packet")
    assert isinstance(packet, dict)
    assert list(packet.get("expected_domains") or []) == []
    assert "get_unchecked" in str(packet.get("non_compliant_code", ""))
    assert packet.get("claim_phrases")


def test_map_publish_record_writes_resolution_report_when_root_provided(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        publish_mapping,
        "gather_candidates",
        lambda packet: ([{"paragraph_id": "fls_unsafe003", "variant_name": "title_focus"}], []),
    )
    monkeypatch.setattr(
        publish_mapping,
        "resolve_fls_for_guideline",
        lambda packet, precomputed_candidates=None, precomputed_variants=None: {
            "paragraph_id": "fls_unsafe003",
            "decision": {"accepted": True, "reason_code": "ACCEPTED"},
        },
    )
    monkeypatch.setattr(
        publish_mapping,
        "validate_fls_id",
        lambda value: value == "fls_unsafe003",
    )
    with patch(
        "retrieval.writer_host.publish_mapping.write_resolution_report",
        return_value=tmp_path / "ret_issue_001.json",
    ):
        mapped = publish_mapping.map_publish_record(
            _row_fixture(),
            resolution_report_root=tmp_path,
        )

    assert str(mapped["fls_resolution_report"]).endswith("ret_issue_001.json")


@pytest.mark.parametrize("fls_candidate", [True, False, "subset-candidate"])
def test_map_publish_record_tolerates_non_dict_fls_candidate(
    monkeypatch,
    fls_candidate,
) -> None:
    row = _row_fixture()
    metadata = dict(cast(dict[str, Any], row["metadata"]))
    metadata["fls_candidate"] = fls_candidate
    row["metadata"] = metadata
    monkeypatch.setattr(
        publish_mapping,
        "gather_candidates",
        lambda packet: ([{"paragraph_id": "fls_unsafe003"}], []),
    )
    monkeypatch.setattr(
        publish_mapping,
        "resolve_fls_for_guideline",
        lambda packet, precomputed_candidates=None, precomputed_variants=None: {
            "paragraph_id": "fls_unsafe003",
            "decision": {"accepted": True, "reason_code": "ACCEPTED"},
        },
    )
    monkeypatch.setattr(publish_mapping, "validate_fls_id", lambda value: value == "fls_unsafe003")

    mapped = publish_mapping.map_publish_record(row)

    assert mapped["guideline_id"].startswith("gui_")
    assert mapped["filename"] == f"{mapped['guideline_id']}.rst"
    assert mapped["title"] == "Preserve safety invariants on error paths"
    assert mapped["category"] == "advisory"


def test_map_publish_record_allows_unresolved_in_review_mode(monkeypatch) -> None:
    monkeypatch.setattr(publish_mapping, "gather_candidates", lambda packet: ([], []))
    monkeypatch.setattr(
        publish_mapping,
        "resolve_fls_for_guideline",
        lambda packet, precomputed_candidates=None, precomputed_variants=None: {
            "paragraph_id": "fls_UNRESOLVED",
            "unresolved_reason": "top candidate chapter mismatches expected domain",
            "decision": {"reason_code": "CHAPTER_MISMATCH", "accepted": False},
        },
    )

    mapped = publish_mapping.map_publish_record(_row_fixture(), allow_unresolved=True)

    assert mapped["fls_id"] == "fls_UNRESOLVED"
    assert mapped["publishability"]["publishable"] is False
    assert mapped["publishability"]["reason_code"] == "CHAPTER_MISMATCH"
