from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from scripts.retrieval.services.s0_phase_a_service import run_scaffold_s0_config
from scripts.retrieval.services.capability import EXIT_UNSUPPORTED


def test_scaffold_s0_soft_retired_returns_typed_unsupported_payload(tmp_path: Path, capsys) -> None:
    _ = tmp_path
    exit_code = run_scaffold_s0_config(Namespace(overwrite=False), root=Path.cwd())
    assert exit_code == EXIT_UNSUPPORTED

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip().splitlines()[0])
    assert payload["status"] == "unsupported_operation"
    assert payload["operation"] == "scaffold-s0-config"
    assert payload["corpus"] == "s0"
