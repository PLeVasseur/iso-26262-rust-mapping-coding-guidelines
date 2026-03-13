from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from opencode_model_registry import ensure_model_available, list_models  # noqa: E402


class _Result:
    def __init__(self, stdout: str) -> None:
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


class OpenCodeModelRegistryTests(unittest.TestCase):
    def test_list_models_parses_provider_output(self) -> None:
        with patch(
            "opencode_model_registry.subprocess.run",
            return_value=_Result("openai/gpt-5.4\nopenai/gpt-5.3-codex\n"),
        ):
            list_models.cache_clear()
            self.assertEqual(list_models("openai"), {"openai/gpt-5.4", "openai/gpt-5.3-codex"})

    def test_ensure_model_available_raises_for_unknown_model(self) -> None:
        with patch(
            "opencode_model_registry.subprocess.run",
            return_value=_Result("openai/gpt-5.4\nopenai/gpt-5.3-codex\n"),
        ):
            list_models.cache_clear()
            with self.assertRaisesRegex(RuntimeError, "configured OpenCode model not available"):
                ensure_model_available("openai/gpt-5.4-codex")
