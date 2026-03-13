from __future__ import annotations

import pytest

from scripts.generate_contract import (
    _change_for_path,
    _purpose_for_path,
    _validate_contract_entries,
)


def test_contract_metadata_templates_non_empty() -> None:
    assert _purpose_for_path("config/s0/s0_gate_policy.yaml")
    assert _purpose_for_path("scripts/retrieval/judges/run_judges.py")
    assert _change_for_path("scripts/integration_checkpoint.py")
    assert _change_for_path("STEP_DEVIATIONS.md")


def test_validate_contract_entries_rejects_blank_fields() -> None:
    contract = {
        "files_created": [{"path": "docs/x.md", "purpose": ""}],
        "files_modified": [{"path": "scripts/y.py", "change": ""}],
    }
    with pytest.raises(ValueError):
        _validate_contract_entries(contract)


def test_validate_contract_entries_accepts_populated_fields() -> None:
    contract = {
        "files_created": [{"path": "docs/x.md", "purpose": "Document rationale."}],
        "files_modified": [{"path": "scripts/y.py", "change": "Refine behavior."}],
    }
    _validate_contract_entries(contract)
