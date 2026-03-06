from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host import publish_mapping  # noqa: E402


def _row_fixture() -> dict[str, object]:
    return {
        "draft": {"target_id": "RET-ISSUE-001"},
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
        "resolve_fls_for_construct",
        lambda terms, expected_domains=None: {
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
        "resolve_fls_for_construct",
        lambda terms, expected_domains=None: {
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
