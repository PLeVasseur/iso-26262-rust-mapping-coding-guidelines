from __future__ import annotations

from pathlib import Path
from typing import Any
import json


def load_style_context(*, run_dir: Path) -> dict[str, Any]:
    candidate = run_dir / "style_context_bundle.json"
    if candidate.exists():
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
    return {
        "global_rules": [
            "Write one reviewable guideline atom, not an umbrella summary.",
            "Use a crisp rule title, not a paraphrase of evidence or a process note.",
            "Keep the amplification short and self-contained; leave justification detail to rationale.",
            "If the evidence is off-target, abstain rather than inventing a recovery guideline.",
        ],
        "amplification_rules": [
            "Prefer a single enforceable code-review question per guideline.",
            "Do not mix chapter families in one operative paragraph.",
        ],
        "example_rules": [
            "Examples must directly exercise the exact rule, not a broad neighboring topic.",
            "Avoid kitchen-sink examples that demonstrate several distinct rule ideas at once.",
        ],
        "rationale_rules": [
            "Rationale explains hazard and consequence; it does not widen the rule scope.",
        ],
        "metadata_bibliography_rules": [
            "Chapter/tags should reflect the real construct family.",
            "Remove exact duplicate bibliography rows and keep evidence curation deliberate.",
        ],
    }
