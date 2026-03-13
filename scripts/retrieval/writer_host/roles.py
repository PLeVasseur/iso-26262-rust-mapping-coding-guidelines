from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from retrieval.writer_host.style_context import load_style_context


def _render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)


def _derive_corpus_placeholder(evidence_rows: list[dict[str, Any]]) -> str:
    corpora = sorted(
        {
            str(row.get("corpus", "")).strip()
            for row in evidence_rows
            if isinstance(row, dict) and str(row.get("corpus", "")).strip()
        }
    )
    if not corpora:
        return ""
    return corpora[0] if len(corpora) == 1 else ",".join(corpora)


def build_role_prompt(
    *,
    role_name: str,
    target_id: str,
    prompt_id: str,
    table1_row: str,
    query_text: str,
    evidence_rows: list[dict[str, Any]],
    prior_outputs: dict[str, dict[str, Any]],
    role_contract: dict[str, Any],
    run_dir: Path | None = None,
    extra_context: dict[str, Any] | None = None,
) -> tuple[str, str]:
    evidence_ids = sorted(
        {
            str(row.get("statement_id") or row.get("top_statement_id") or "").strip()
            for row in evidence_rows
            if isinstance(row, dict)
        }
    )
    evidence_ids = [value for value in evidence_ids if value]
    snippets = []
    for row in evidence_rows[:5]:
        if not isinstance(row, dict):
            continue
        snippets.append(
            {
                "evidence_id": str(
                    row.get("statement_id") or row.get("top_statement_id") or ""
                ).strip(),
                "source_anchor": str(
                    row.get("source_anchor") or row.get("top_source_anchor") or ""
                ).strip(),
                "statement_text": str(
                    row.get("statement_text") or row.get("chunk_text") or ""
                ).strip(),
                "final_score": float(row.get("final_score") or row.get("score") or 0.0),
            }
        )

    template = str(role_contract.get("prompt_template_text", "")).strip()
    corpus_placeholder = _derive_corpus_placeholder(evidence_rows)
    style_context = (
        load_style_context(run_dir=run_dir)
        if run_dir is not None
        else load_style_context(run_dir=Path("."))
    )

    def _style_lines(key: str, fallback: str) -> str:
        value = style_context.get(key)
        if isinstance(value, list):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            if cleaned:
                return "\n".join(f"- {item}" for item in cleaned)
        return fallback

    placeholders = {
        "target_id": target_id,
        "table1_row": table1_row,
        "corpus": corpus_placeholder,
        "evidence_ids": _to_json(evidence_ids),
        "evidence_snippets": _to_json(snippets),
        "exemplar_ids": _to_json(list(prior_outputs.get("selected_exemplar_ids", []))),
        "global_rules": _style_lines(
            "global_rules",
            "- Do not fabricate evidence.\n- Return JSON only.",
        ),
        "evidence_synthesis": _to_json(prior_outputs.get("evidence_synthesizer", {})),
        "amplification": _to_json(prior_outputs.get("amplification_author", {})),
        "examples": _to_json(prior_outputs.get("example_author", {})),
        "all_writer_outputs": _to_json(prior_outputs),
        "amplification_rules": _style_lines(
            "amplification_rules",
            "- Prefer explicit, actionable controls.",
        ),
        "example_rules": _style_lines(
            "example_rules",
            "- Provide concise, compile-oriented snippets.",
        ),
        "rationale_rules": _style_lines(
            "rationale_rules",
            "- Use hazard->mechanism->consequence chain.",
        ),
        "metadata_bibliography_rules": _style_lines(
            "metadata_bibliography_rules",
            "- Keep citation keys stable and auditable.",
        ),
        "planner_rules": _style_lines(
            "planner_rules",
            "- Plan the smallest set of supportable rule atoms.",
        ),
        "curator_rules": _style_lines(
            "curator_rules",
            "- Keep the smallest non-overlapping set of atoms.",
        ),
        "planned_atoms": _to_json((extra_context or {}).get("planned_atoms", [])),
        "planned_atom": _to_json((extra_context or {}).get("planned_atom", {})),
        "candidate_drafts": _to_json((extra_context or {}).get("candidate_drafts", [])),
        "batch_overlap_candidates": _to_json(
            (extra_context or {}).get("batch_overlap_candidates", [])
        ),
        "baseline_overlap_candidates": _to_json(
            (extra_context or {}).get("baseline_overlap_candidates", [])
        ),
        "planning_schema_hint": _to_json((extra_context or {}).get("planning_schema_hint", {})),
    }
    rendered = _render_template(template, placeholders)

    required_output_schema = role_contract.get("required_output_schema")
    required_fields: list[str] = []
    if isinstance(required_output_schema, dict):
        required_fields = [str(item) for item in list(required_output_schema.get("required") or [])]
    evidence_corpora = sorted(corpus_placeholder.split(",")) if corpus_placeholder else []

    prompt = (
        f"{rendered}\n\n"
        "Return one JSON object only (no markdown fences, no commentary).\n"
        f"Role: {role_name}\n"
        f"Prompt ID: {prompt_id}\n"
        f"Query text: {query_text}\n"
        f"Evidence corpora: {json.dumps(evidence_corpora)}\n"
        f"Required fields: {json.dumps(required_fields)}\n"
        f"Available evidence IDs: {json.dumps(evidence_ids)}\n"
        f"Evidence snippets: {json.dumps(snippets)}\n"
        f"Prior role outputs: {json.dumps(prior_outputs, sort_keys=True)}\n"
    )
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return prompt, prompt_hash


def extract_construct_terms(output: dict[str, Any]) -> list[str]:
    construct_scope = output.get("construct_scope")
    if isinstance(construct_scope, list):
        return [str(item) for item in construct_scope if str(item).strip()]
    return []


def extract_claim_map(output: dict[str, Any]) -> list[dict[str, Any]]:
    claim_map = output.get("claim_to_evidence_map")
    if not isinstance(claim_map, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in claim_map:
        if isinstance(row, dict):
            rows.append(row)
    return rows
