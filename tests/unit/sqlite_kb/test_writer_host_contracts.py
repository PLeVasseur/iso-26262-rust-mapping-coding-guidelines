from __future__ import annotations

from pathlib import Path

from retrieval.writer_host.contracts import build_contract_snapshot, load_contracts


def test_load_contracts_and_snapshot_contains_all_roles() -> None:
    root = Path(__file__).resolve().parents[3]
    payload = load_contracts(root / "config" / "s0" / "writer_prompt_contracts.yaml")
    snapshot = build_contract_snapshot(payload)
    roles = snapshot.get("roles")
    assert isinstance(roles, dict)
    for role_name in (
        "evidence_synthesizer",
        "amplification_author",
        "example_author",
        "rationale_author",
        "metadata_citation_curator",
    ):
        assert role_name in roles
        role_row = roles[role_name]
        assert role_row.get("required_output_fields")
