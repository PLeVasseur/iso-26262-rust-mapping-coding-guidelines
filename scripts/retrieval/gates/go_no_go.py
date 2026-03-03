from __future__ import annotations

from typing import Any


def evaluate_gates(
    *,
    evidence_gate_status: str,
    citation_resolution_status: str,
    output_conformance_status: str,
    candidate_grade_count: int,
    review_count: int,
    abstain_rate: float,
    scope_blocked_count: int = 0,
) -> dict[str, Any]:
    gate_passed = (
        evidence_gate_status == "pass"
        and citation_resolution_status == "pass"
        and output_conformance_status == "pass"
        and candidate_grade_count >= 3
        and review_count == 0
        and abstain_rate <= 0.40
    )
    return {
        "gate_passed": gate_passed,
        "evidence_gate_status": evidence_gate_status,
        "citation_resolution_status": citation_resolution_status,
        "output_conformance_status": output_conformance_status,
        "candidate_grade_count": candidate_grade_count,
        "review_count": review_count,
        "abstain_rate": abstain_rate,
        "scope_blocked_count": scope_blocked_count,
        "verdict_model": "binary_pass_fail",
    }


def compute_go_no_go(gates: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "pass" if bool(gates.get("gate_passed", False)) else "fail",
        **gates,
    }
