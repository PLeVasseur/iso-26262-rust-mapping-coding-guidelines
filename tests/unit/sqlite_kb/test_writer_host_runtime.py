from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from retrieval.writer_host.runtime import run


def test_writer_host_runtime_dry_run_writes_contract_snapshot(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    report_root = tmp_path / "writer-host-run"
    args = Namespace(
        corpus="rust_reference",
        profile_path="",
        extra_args=[],
        targets="RET-ISSUE-005",
        run_id="",
        report_root=str(report_root),
        contract_path="config/s0/writer_prompt_contracts.yaml",
        query_testset_path="data/query_testsets/rust_reference_table1_retrieval_eval.yaml",
        query_mode="lexical",
        top_k=10,
        max_retries=1,
        dry_run=True,
    )
    exit_code = run(args, root=root)
    assert exit_code == 0
    snapshot_path = report_root / "writer_subagent_outputs" / "prompt_contract_snapshot.json"
    assert snapshot_path.exists()
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert "roles" in payload
