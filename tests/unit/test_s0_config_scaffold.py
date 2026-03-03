from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import yaml

from scripts.retrieval.services.s0_phase_a_service import run_scaffold_s0_config


def test_scaffold_s0_gate_policy_includes_step8_retry_budget_keys(tmp_path: Path) -> None:
    exit_code = run_scaffold_s0_config(Namespace(overwrite=False), root=tmp_path)
    assert exit_code == 0

    policy_path = tmp_path / "config" / "s0" / "s0_gate_policy.yaml"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))

    assert policy["convention_retry_budget"] == 50
    assert policy["compilation_retry_budget"] == 15
    assert policy["max_convention_retries"] == 50
    assert policy["max_compilation_retries"] == 15
    assert policy["max_judge_calls"] == 70
