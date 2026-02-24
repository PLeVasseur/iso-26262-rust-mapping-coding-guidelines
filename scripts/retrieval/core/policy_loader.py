from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_eval_policy(policy_path: Path) -> dict[str, Any]:
    with policy_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"Eval policy must be a mapping: {policy_path}")
    return payload
