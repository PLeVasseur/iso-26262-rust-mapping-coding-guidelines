from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.roles import build_role_prompt  # noqa: E402


def test_build_role_prompt_derives_corpus_context_from_evidence_rows() -> None:
    role_contract = {
        "prompt_template_text": "Evidence corpus context: {{corpus}}",
        "required_output_schema": {"required": ["target_id"]},
    }
    prompt, _ = build_role_prompt(
        role_name="evidence_synthesizer",
        target_id="RET-ISSUE-001",
        prompt_id="RET-ISSUE-001",
        table1_row="1d",
        query_text="query",
        evidence_rows=[
            {
                "statement_id": "core_docs::stmt-1",
                "corpus": "core_docs",
                "source_anchor": "https://example.test/core",
                "statement_text": "Core evidence",
                "score": 0.9,
            }
        ],
        prior_outputs={},
        role_contract=role_contract,
    )
    assert "Evidence corpus context: core_docs" in prompt
    assert 'Evidence corpora: ["core_docs"]' in prompt
    assert "rust_reference" not in prompt


def test_build_role_prompt_lists_multiple_corpora() -> None:
    role_contract = {
        "prompt_template_text": "Evidence corpus context: {{corpus}}",
        "required_output_schema": {"required": ["target_id"]},
    }
    prompt, _ = build_role_prompt(
        role_name="evidence_synthesizer",
        target_id="RET-ISSUE-001",
        prompt_id="RET-ISSUE-001",
        table1_row="1d",
        query_text="query",
        evidence_rows=[
            {
                "statement_id": "core_docs::stmt-1",
                "corpus": "core_docs",
                "source_anchor": "https://example.test/core",
                "statement_text": "Core evidence",
                "score": 0.9,
            },
            {
                "statement_id": "rust_reference::stmt-2",
                "corpus": "rust_reference",
                "source_anchor": "https://example.test/rust",
                "statement_text": "Rust evidence",
                "score": 0.8,
            },
        ],
        prior_outputs={},
        role_contract=role_contract,
    )
    assert "Evidence corpus context: core_docs,rust_reference" in prompt
    assert 'Evidence corpora: ["core_docs", "rust_reference"]' in prompt
