from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.retry import RetryOutcome  # noqa: E402
from retrieval.writer_host.runtime import run  # noqa: E402


def _write_evidence_manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "corpora": ["rust_reference", "core_docs"],
                "targets": [
                    {
                        "target_id": "RET-ISSUE-005",
                        "query_text": "ownership safety",
                        "expected_row_markers": ["1e"],
                        "selected_evidence": [
                            {
                                "statement_id": "rust_reference::stmt-1",
                                "raw_statement_id": "stmt-1",
                                "corpus": "rust_reference",
                                "source_anchor": "https://example.test/rust_reference",
                                "doc_id": "rust-doc",
                                "statement_text": "Ownership evidence",
                                "score": 0.9,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_writer_host_runtime_dry_run_writes_contract_snapshot(tmp_path: Path) -> None:
    root = ROOT
    report_root = tmp_path / "writer-host-run"
    manifest_path = tmp_path / "writer_evidence_manifest.json"
    _write_evidence_manifest(manifest_path)
    args = Namespace(
        extra_args=[],
        run_id="",
        report_root=str(report_root),
        evidence_manifest=str(manifest_path),
        contract_path="config/s0/writer_prompt_contracts.yaml",
        max_retries=1,
        model="",
        agent="",
        dry_run=True,
    )
    exit_code = run(args, root=root)
    assert exit_code == 0
    snapshot_path = report_root / "writer_subagent_outputs" / "prompt_contract_snapshot.json"
    assert snapshot_path.exists()
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert "roles" in payload
    summary = json.loads((report_root / "writer_host_run_summary.json").read_text(encoding="utf-8"))
    assert summary["evidence_manifest"] == str(manifest_path.resolve())
    assert summary["corpora"] == ["rust_reference", "core_docs"]
    progress = json.loads(
        (report_root / "writer_execution_progress.json").read_text(encoding="utf-8")
    )
    assert progress["status"] == "dry_run"
    assert progress["completed_target_count"] == 1


def test_writer_host_runtime_writes_progress_during_execution(tmp_path: Path) -> None:
    root = ROOT
    report_root = tmp_path / "writer-host-run"
    manifest_path = tmp_path / "writer_evidence_manifest.json"
    _write_evidence_manifest(manifest_path)
    args = Namespace(
        extra_args=[],
        run_id="",
        report_root=str(report_root),
        evidence_manifest=str(manifest_path),
        contract_path="config/s0/writer_prompt_contracts.yaml",
        max_retries=1,
        model="",
        agent="",
        dry_run=False,
    )

    base_output = {
        "target_id": "RET-ISSUE-005",
        "prompt_id": "RET-ISSUE-005",
        "hazard": "hazard",
        "mechanism": "mechanism",
        "mitigation": "mitigation",
        "construct_scope": ["core::ptr::read"],
        "evidence_ids": ["rust_reference::stmt-1"],
        "claim_to_evidence_map": [
            {
                "claim_id": "RET-ISSUE-005::claim::1",
                "claim_text": "claim",
                "evidence_refs": [{"evidence_id": "rust_reference::stmt-1"}],
            }
        ],
        "guideline_amplification_text": "Amplify",
        "normative_strength": "shall",
        "amplification_citation_keys": ["cite-1"],
        "non_compliant_narrative": "bad",
        "non_compliant_code": "unsafe {}",
        "compliant_narrative": "good",
        "compliant_code": "fn ok() {}",
        "example_citation_keys": ["cite-1"],
        "non_compliant_miri_intent": "skip",
        "compliant_miri_intent": "check",
        "non_compliant_miri_skip_justification": "n/a",
        "rationale_text": "Because",
        "hazard_mechanism_consequence_map": [{"hazard": "h", "mechanism": "m", "consequence": "c"}],
        "rationale_citation_keys": ["cite-1"],
        "tags": ["tag"],
        "fls_candidate": "FLS-1",
        "bibliography_rows": [{"id": "bib-1"}],
        "citation_key_map": {"cite-1": "rust_reference::stmt-1"},
        "metadata_validation_notes": ["ok"],
    }

    outputs = {
        "evidence_synthesizer": dict(base_output),
        "amplification_author": {
            "target_id": "RET-ISSUE-005",
            "guideline_amplification_text": "Amplify",
            "normative_strength": "shall",
            "amplification_citation_keys": ["cite-1"],
        },
        "example_author": {
            "target_id": "RET-ISSUE-005",
            "non_compliant_narrative": "bad",
            "non_compliant_code": "unsafe {}",
            "compliant_narrative": "good",
            "compliant_code": "fn ok() {}",
            "example_citation_keys": ["cite-1"],
            "non_compliant_miri_intent": "skip",
            "compliant_miri_intent": "check",
            "non_compliant_miri_skip_justification": "n/a",
        },
        "rationale_author": {
            "target_id": "RET-ISSUE-005",
            "rationale_text": "Because",
            "hazard_mechanism_consequence_map": [
                {"hazard": "h", "mechanism": "m", "consequence": "c"}
            ],
            "rationale_citation_keys": ["cite-1"],
        },
        "metadata_citation_curator": {
            "target_id": "RET-ISSUE-005",
            "tags": ["tag"],
            "fls_candidate": "FLS-1",
            "bibliography_rows": [{"id": "bib-1"}],
            "citation_key_map": {"cite-1": "rust_reference::stmt-1"},
            "metadata_validation_notes": ["ok"],
        },
    }

    def fake_run_role_with_retry(*, role_name: str, **_: object) -> RetryOutcome:
        return RetryOutcome(
            output=outputs[role_name],
            attempts=1,
            violations=[],
            oscillation_detected=False,
            diminishing_returns=False,
            budget_exhausted=False,
            session_id=f"test::{role_name}",
            failure_kind=None,
            failure_detail="",
        )

    with patch(
        "retrieval.writer_host.runtime.run_role_with_retry", side_effect=fake_run_role_with_retry
    ):
        exit_code = run(args, root=root)

    assert exit_code == 0
    progress = json.loads(
        (report_root / "writer_execution_progress.json").read_text(encoding="utf-8")
    )
    assert progress["status"] == "completed"
    assert progress["completed_target_count"] == 1
    assert progress["completed_roles"] == 5


def test_writer_host_runtime_normalizes_synth_prompt_aliases_and_citation_map(
    tmp_path: Path,
) -> None:
    root = ROOT
    report_root = tmp_path / "writer-host-run"
    manifest_path = tmp_path / "writer_evidence_manifest.json"
    _write_evidence_manifest(manifest_path)
    args = Namespace(
        extra_args=[],
        run_id="",
        report_root=str(report_root),
        evidence_manifest=str(manifest_path),
        contract_path="config/s0/writer_prompt_contracts.yaml",
        max_retries=1,
        model="",
        agent="",
        dry_run=False,
    )

    outputs = {
        "evidence_synthesizer": {
            "target_id": "RET-ISSUE-005",
            "prompt_id": "EXAMPLE-001",
            "hazard": "hazard",
            "mechanism": "mechanism",
            "mitigation": "mitigation",
            "construct_scope": ["core::ptr::read"],
            "evidence_ids": ["rust_reference::stmt-1"],
            "claim_to_evidence_map": [
                {
                    "claim_id": "EXAMPLE-001::claim::1",
                    "claim_text": "claim",
                    "evidence_refs": [{"evidence_id": "rust_reference::stmt-1"}],
                }
            ],
        },
        "amplification_author": {
            "target_id": "RET-ISSUE-005",
            "guideline_amplification_text": "Amplify",
            "normative_strength": "shall",
            "amplification_citation_keys": ["cite-1"],
        },
        "example_author": {
            "target_id": "RET-ISSUE-005",
            "non_compliant_narrative": "bad",
            "non_compliant_code": "unsafe {}",
            "compliant_narrative": "good",
            "compliant_code": "fn ok() {}",
            "example_citation_keys": ["rust_reference::stmt-2"],
            "non_compliant_miri_intent": "skip",
            "compliant_miri_intent": "check",
            "non_compliant_miri_justification": "legacy alias",
        },
        "rationale_author": {
            "target_id": "RET-ISSUE-005",
            "rationale_text": "Because",
            "hazard_mechanism_consequence_map": [
                {"hazard": "h", "mechanism": "m", "consequence": "c"}
            ],
            "rationale_citation_keys": ["cite-1"],
        },
        "metadata_citation_curator": {
            "target_id": "RET-ISSUE-005",
            "tags": ["tag"],
            "fls_candidate": "FLS-1",
            "bibliography_rows": [{"citation_key": "cite-1"}],
            "citation_key_map": {"rust_reference::stmt-1": "cite-1"},
            "metadata_validation_notes": ["ok"],
        },
    }

    def fake_run_role_with_retry(*, role_name: str, **_: object) -> RetryOutcome:
        return RetryOutcome(
            output=outputs[role_name],
            attempts=1,
            violations=[],
            oscillation_detected=False,
            diminishing_returns=False,
            budget_exhausted=False,
            session_id=f"test::{role_name}",
            failure_kind=None,
            failure_detail="",
        )

    with patch(
        "retrieval.writer_host.runtime.run_role_with_retry", side_effect=fake_run_role_with_retry
    ):
        exit_code = run(args, root=root)

    assert exit_code == 0
    synth_rows = [
        json.loads(line)
        for line in (report_root / "writer_subagent_outputs" / "evidence_synthesizer.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    synth = synth_rows[0]["output"]
    assert synth["prompt_id"] == "RET-ISSUE-005"
    assert synth["claim_to_evidence_map"][0]["claim_id"] == "RET-ISSUE-005::claim::1"

    example_rows = [
        json.loads(line)
        for line in (report_root / "writer_subagent_outputs" / "example_author.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert example_rows[0]["output"]["non_compliant_miri_skip_justification"] == "legacy alias"
    assert example_rows[0]["output"]["example_citation_keys"] == ["rust_reference::stmt-1"]

    metadata_rows = [
        json.loads(line)
        for line in (report_root / "writer_subagent_outputs" / "metadata_citation_curator.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert metadata_rows[0]["output"]["citation_key_map"] == {"cite-1": "rust_reference::stmt-1"}

    trace = json.loads(
        (report_root / "writer_subagent_outputs" / "subagent_invocation_trace.json").read_text(
            encoding="utf-8"
        )
    )["entries"]
    evidence_trace = next(item for item in trace if item["role"] == "evidence_synthesizer")
    assert evidence_trace["normalization_fallback_applied"] is True
    assert "prompt_id_mismatch_rewritten" in evidence_trace["normalization_changes"]
    example_trace = next(item for item in trace if item["role"] == "example_author")
    assert (
        "example_citation_keys_evidence_id_typo_corrected" in example_trace["normalization_changes"]
    )


def test_writer_host_runtime_preserves_transport_failure_without_normalization(
    tmp_path: Path,
) -> None:
    root = ROOT
    report_root = tmp_path / "writer-host-run"
    manifest_path = tmp_path / "writer_evidence_manifest.json"
    _write_evidence_manifest(manifest_path)
    args = Namespace(
        extra_args=[],
        run_id="",
        report_root=str(report_root),
        evidence_manifest=str(manifest_path),
        contract_path="config/s0/writer_prompt_contracts.yaml",
        max_retries=1,
        model="openai/gpt-5.4",
        agent="",
        dry_run=False,
    )

    outcomes = {
        "evidence_synthesizer": RetryOutcome(
            output={},
            attempts=2,
            violations=["model_not_found"],
            oscillation_detected=False,
            diminishing_returns=False,
            budget_exhausted=True,
            session_id="test::evidence_synthesizer",
            failure_kind="model_not_found",
            failure_detail="configured OpenCode model not available",
        ),
        "amplification_author": RetryOutcome(
            output={},
            attempts=1,
            violations=["transport_failure"],
            oscillation_detected=False,
            diminishing_returns=False,
            budget_exhausted=True,
            session_id="test::amplification_author",
            failure_kind="transport_failure",
            failure_detail="transport failed",
        ),
        "example_author": RetryOutcome(
            output={},
            attempts=1,
            violations=["transport_failure"],
            oscillation_detected=False,
            diminishing_returns=False,
            budget_exhausted=True,
            session_id="test::example_author",
            failure_kind="transport_failure",
            failure_detail="transport failed",
        ),
        "rationale_author": RetryOutcome(
            output={},
            attempts=1,
            violations=["transport_failure"],
            oscillation_detected=False,
            diminishing_returns=False,
            budget_exhausted=True,
            session_id="test::rationale_author",
            failure_kind="transport_failure",
            failure_detail="transport failed",
        ),
        "metadata_citation_curator": RetryOutcome(
            output={},
            attempts=1,
            violations=["transport_failure"],
            oscillation_detected=False,
            diminishing_returns=False,
            budget_exhausted=True,
            session_id="test::metadata_citation_curator",
            failure_kind="transport_failure",
            failure_detail="transport failed",
        ),
    }

    def fake_run_role_with_retry(*, role_name: str, **_: object) -> RetryOutcome:
        return outcomes[role_name]

    with (
        patch("retrieval.writer_host.runtime.ensure_model_available", return_value=None),
        patch(
            "retrieval.writer_host.runtime.run_role_with_retry",
            side_effect=fake_run_role_with_retry,
        ),
    ):
        exit_code = run(args, root=root)

    assert exit_code == 2
    evidence_rows = (
        report_root / "writer_subagent_outputs" / "evidence_synthesizer.jsonl"
    ).read_text(encoding="utf-8")
    assert '"prompt_id": "RET-ISSUE-005"' not in evidence_rows
    trace = json.loads(
        (report_root / "writer_subagent_outputs" / "subagent_invocation_trace.json").read_text(
            encoding="utf-8"
        )
    )
    first = trace["entries"][0]
    assert first["failure_kind"] == "model_not_found"
    assert first["normalization_fallback_applied"] is False
