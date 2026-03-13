from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path


def _parse_models(stdout: str) -> set[str]:
    return {line.strip() for line in stdout.splitlines() if line.strip()}


@lru_cache(maxsize=8)
def list_models(provider: str = "") -> set[str]:
    command = ["opencode", "models"]
    provider_text = str(provider).strip()
    if provider_text:
        command.append(provider_text)
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if int(result.returncode) != 0:
        stderr = (result.stderr or result.stdout).strip()
        raise RuntimeError(stderr or "failed to list OpenCode models")
    return _parse_models(result.stdout)


def ensure_model_available(model: str) -> None:
    model_text = str(model).strip()
    if not model_text:
        return
    provider = model_text.split("/", 1)[0] if "/" in model_text else ""
    available = list_models(provider)
    if model_text not in available:
        raise RuntimeError(
            f"configured OpenCode model not available: {model_text}; "
            f"check `opencode models {provider or ''}`"
        )


def configured_default_model(config_path: Path | None = None) -> str:
    path = config_path or Path("opencode.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload.get("model", "")).strip()
