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
        "resolve_fls_for_guideline",
        lambda packet: {
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
        "resolve_fls_for_guideline",
        lambda packet: {
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

    def fake_resolve(packet):
        seen["packet"] = packet
        return {
            "paragraph_id": "fls_unsafe003",
            "decision": {"reason_code": "ACCEPTED", "accepted": True},
        }

    monkeypatch.setattr(publish_mapping, "resolve_fls_for_guideline", fake_resolve)
    monkeypatch.setattr(publish_mapping, "validate_fls_id", lambda value: value == "fls_unsafe003")

    publish_mapping.map_publish_record(_row_fixture())

    packet = seen.get("packet")
    assert isinstance(packet, dict)
    assert set(packet) == {
        "governing_obligation",
        "construct_terms",
        "code_tokens",
        "supporting_phrases",
        "prior_documents",
        "prior_sections",
        "ambiguity_notes",
    }
    assert "get_unchecked" in list(packet.get("code_tokens") or [])
    assert packet.get("supporting_phrases")
    assert "expected_domains" not in packet


def test_map_publish_record_writes_resolution_report_when_root_provided(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        publish_mapping,
        "resolve_fls_for_guideline",
        lambda packet: {
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


def test_map_publish_record_passes_title_and_target_to_report_writer(
    monkeypatch,
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        publish_mapping,
        "resolve_fls_for_guideline",
        lambda packet: {
            "paragraph_id": "fls_unsafe003",
            "decision": {"accepted": True, "reason_code": "ACCEPTED"},
        },
    )
    monkeypatch.setattr(publish_mapping, "validate_fls_id", lambda value: value == "fls_unsafe003")

    def fake_write_resolution_report(*, report_root, target_id, title, payload):
        seen["target_id"] = target_id
        seen["title"] = title
        seen["payload"] = payload
        return tmp_path / "ret_issue_001.json"

    monkeypatch.setattr(publish_mapping, "write_resolution_report", fake_write_resolution_report)

    publish_mapping.map_publish_record(_row_fixture(), resolution_report_root=tmp_path)

    assert seen["target_id"] == "RET-ISSUE-001"
    assert seen["title"] == "Preserve safety invariants on error paths"
    payload = cast(dict[str, Any], seen["payload"])
    assert payload["runtime_mode"] == "grounding_only_ws6"
    assert "grounding_packet" in payload
    assert "candidate_count" not in payload
    assert "candidate_preview" not in payload


def test_map_publish_record_metadata_routing_fields_do_not_change_fls_packet(monkeypatch) -> None:
    packets: list[dict[str, object]] = []

    def fake_resolve(packet: dict[str, object]):
        packets.append(dict(packet))
        return {
            "paragraph_id": "fls_unsafe003",
            "decision": {"accepted": True, "reason_code": "ACCEPTED"},
        }

    monkeypatch.setattr(publish_mapping, "resolve_fls_for_guideline", fake_resolve)
    monkeypatch.setattr(publish_mapping, "validate_fls_id", lambda value: value == "fls_unsafe003")

    base = _row_fixture()
    altered = _row_fixture()
    altered["draft"] = dict(cast(dict[str, Any], altered["draft"]))
    altered["metadata"] = {
        "tags": ["concurrency", "ffi", "macros"],
        "editorial_metadata": {"proposed_title": "Editorial override should stay inert"},
        "fls_candidate": {
            "statement": "Metadata should not influence grounding",
            "category": "mandatory",
        },
    }

    publish_mapping.map_publish_record(base)
    publish_mapping.map_publish_record(altered)

    assert len(packets) == 2
    assert packets[0] == packets[1]


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
        "resolve_fls_for_guideline",
        lambda packet: {
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
    monkeypatch.setattr(
        publish_mapping,
        "resolve_fls_for_guideline",
        lambda packet: {
            "paragraph_id": "fls_UNRESOLVED",
            "unresolved_reason": "top candidate chapter mismatches expected domain",
            "decision": {"reason_code": "CHAPTER_MISMATCH", "accepted": False},
        },
    )

    mapped = publish_mapping.map_publish_record(_row_fixture(), allow_unresolved=True)

    assert mapped["fls_id"] == "fls_UNRESOLVED"
    assert mapped["publishability"]["publishable"] is False
    assert mapped["publishability"]["reason_code"] == "CHAPTER_MISMATCH"


def test_map_publish_record_uses_grounding_only_runtime_by_default(monkeypatch) -> None:
    monkeypatch.setattr(publish_mapping, "validate_fls_id", lambda value: value.startswith("fls_"))

    mapped = publish_mapping.map_publish_record(_row_fixture(), allow_unresolved=True)

    assert mapped["fls_id"] == "fls_UNRESOLVED"
    assert mapped["fls_resolution"]["reason_code"] == "WS7_REQUIRED"
    assert mapped["publishability"]["publishable"] is False
