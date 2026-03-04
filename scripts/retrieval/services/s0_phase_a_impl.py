from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import yaml

from context.convention_extractor import extract_all_exemplar_conventions
from context.convention_spec import _build_convention_spec, _diff_specs, validate_convention_spec
from context.exemplars import get_exemplar_paths
from context.fls_lookup import get_fls_db_stats, resolve_fls_for_construct, validate_fls_id
from context.stdlib_lookup import CORE_DOCS_DB_PATH, load_stdlib_index
from retrieval.judges.pipeline_adapter import execute_stage_b_pipeline
from retrieval.rendering.rst_renderer import RendererInput, render_guideline_rst
from retrieval.services.calibration_artifacts import (
    run_enforce_calibration_quality as _run_enforce_calibration_quality,
)
from retrieval.services.calibration_artifacts import (
    run_pack_reviewer_packet as _run_pack_reviewer_packet,
)
from retrieval.validation.conformance import validate_generated_rst_conformance
from scripts.opencode_retry_wrapper import CONVENTION_RETRY_BUDGET, retry_with_violations
from retrieval.services.utils import (
    _now_id,
    _read_json,
    _read_jsonl,
    _report_dir,
    _run_id,
    _write_json,
)
from scripts.validate_fls_matching import validate_fls_matching
from validation.role_validators import RoleViolation, validate_role_output


EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def _safe_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {}


def _canonical_payload_digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _build_calibration_fingerprint(
    *,
    mode: str,
    profile: str,
    targets_payload: dict[str, Any],
    writer_contracts: dict[str, Any],
    judge_contracts: dict[str, Any],
    writer_model: str,
    judge_model: str,
) -> dict[str, Any]:
    fingerprint = {
        "schema_version": "calibration_resume_v1",
        "mode": mode,
        "profile": profile,
        "targets_digest": _canonical_payload_digest(targets_payload),
        "writer_contracts_digest": _canonical_payload_digest(writer_contracts),
        "judge_contracts_digest": _canonical_payload_digest(judge_contracts),
        "writer_model": writer_model,
        "judge_model": judge_model,
    }
    return {
        "schema_version": "calibration_resume_v1",
        "fingerprint": fingerprint,
        "fingerprint_digest": _canonical_payload_digest(fingerprint),
    }


def _is_allowed_resume_artifact(name: str) -> bool:
    allowed_exact = {
        "targets.json",
        "targets_digest",
        "startup_checklist_report.json",
        "calibration_target_rationale.json",
        "calibration_resume_fingerprint.json",
        "resume_state.json",
        "core_docs_eval_report.json",
        "rust_reference_eval_report.json",
        "doctor_report.json",
        "doctor_quality_minimums_report.json",
        "worked_example_validation_report.json",
        "prompt_contract_validation_report.json",
        "catalog_smoke_report.json",
        "build_env_fingerprint.json",
        "embedding_backend_fingerprint.json",
        "golden_exemplar_lock_report.json",
        "shape_validation_report.json",
        "format_diff_report.json",
        "golden_shape_comparator_report.json",
        "exemplar_usage_auditor_report.json",
        "writer_output_auditor_report.json",
        "normalization_report.json",
        "evidence_synthesizer_gate_report.json",
        "citation_resolution_report.json",
        "duplicate_similarity_gate_report.json",
        "construct_evidence_alignment_report.json",
        "example_execution_semantics_report.json",
        "modality_category_consistency_report.json",
        "judge_aggregate.json",
        "run_budget_report.json",
        "summary.json",
        "README.md",
        "calibration_report.json",
        "quality_report.json",
        "novelty_report.json",
        "embarrassing_failures_observed.json",
        "export_manifest.json",
        "retrieval_diagnostics.json",
        "duplicate_similarity_matrix.json",
        "calibration_quality_enforcement_report.json",
        "packet_manifest.json",
        "convention_spec.json",
        "convention_spec_validation.json",
        "convention_spec_diff.json",
        "lookup_status.json",
        "fls_matching_validation.json",
        "role_validation_report.json",
        "guideline_manifest.json",
    }
    if name in allowed_exact:
        return True
    if name in {
        "evidence_bundle",
        "writer_subagent_outputs",
        "judge_passes",
        "stage_b_judges",
        "generated_guidelines_rst",
        "rerendered_rst",
    }:
        return True
    if name.endswith("_backend_attempts.jsonl"):
        return True
    if name.endswith(".jsonl") and (
        name.startswith("drafts")
        or name.startswith("analysis_memos")
        or name.startswith("exemplar_selection_trace")
        or name.startswith("synthesis_input_trace")
        or name.startswith("stage_b_judge_invocations")
    ):
        return True
    return False


def _canonical_bytes(text: str) -> bytes:
    normalized = "\n".join(
        part.rstrip() for part in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    )
    return normalized.encode("utf-8")


def _canonical_digest(text: str) -> str:
    return hashlib.sha256(_canonical_bytes(text)).hexdigest()


def _approx_tokens(text: str) -> int:
    return int(len(text.split()) * 1.3)


def _truncate_mapping_for_budget(
    mapping: dict[str, str],
    *,
    token_budget: int,
    sort_terms: list[str],
) -> tuple[dict[str, str], int]:
    terms = [term.lower() for term in sort_terms if term]

    def _score(item: tuple[str, str]) -> tuple[int, int, str]:
        key, value = item
        key_l = key.lower()
        value_l = value.lower()
        overlap = sum(1 for term in terms if term in key_l or term in value_l)
        return (-overlap, len(value), key)

    ordered = sorted(mapping.items(), key=_score)
    kept: dict[str, str] = {}
    omitted = 0
    for key, value in ordered:
        probe = {**kept, key: value}
        as_text = "\n".join(f"{k} -> {v}" for k, v in probe.items())
        if _approx_tokens(as_text) > token_budget:
            omitted += 1
            continue
        kept[key] = value
    if omitted:
        kept["[TRUNCATED]"] = f"[TRUNCATED - {omitted} entries omitted]"
    return kept, omitted


def _truncate_exemplar_extracts(
    extracts: list[dict[str, str]], *, token_budget: int
) -> tuple[list[dict[str, str]], int]:
    trimmed: list[dict[str, str]] = []
    omitted = 0
    for extract in extracts:
        probe = trimmed + [extract]
        if _approx_tokens(json.dumps(probe, sort_keys=True)) > token_budget:
            omitted += 1
            continue
        trimmed.append(extract)
    if omitted:
        trimmed.append(
            {
                "guideline_id": "[TRUNCATED]",
                "snippet": f"[TRUNCATED - {omitted} entries omitted]",
            }
        )
    return trimmed, omitted


def _is_relevant_to_construct(std_short_name: str, construct_terms: list[str]) -> bool:
    if not construct_terms:
        return True
    key = std_short_name.lower()
    return any(term.lower() in key or key in term.lower() for term in construct_terms)


def _shingle_jaccard(a: str, b: str, n: int = 4) -> float:
    def _shingles(value: str) -> set[str]:
        tokens = value.lower().split()
        if len(tokens) < n:
            return {" ".join(tokens)} if tokens else set()
        return {" ".join(tokens[idx : idx + n]) for idx in range(0, len(tokens) - n + 1)}

    a_set = _shingles(a)
    b_set = _shingles(b)
    if not a_set and not b_set:
        return 1.0
    if not a_set or not b_set:
        return 0.0
    return len(a_set & b_set) / len(a_set | b_set)


def _normalize_text(value: str) -> str:
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").lower().split())


def _extract_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?", "", candidate).strip()
        if candidate.endswith("```"):
            candidate = candidate[:-3].strip()
    if candidate.startswith("{") and candidate.endswith("}"):
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {}
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(candidate[start : end + 1])
        return parsed if isinstance(parsed, dict) else {}
    raise ValueError("No JSON object found in model response")


def _required_fields(payload: dict[str, Any]) -> list[str]:
    if not isinstance(payload, dict):
        return []
    required = payload.get("required")
    if not isinstance(required, list):
        return []
    return [str(item) for item in required]


def _ensure_required_fields(role: str, output: dict[str, Any], required: list[str]) -> list[str]:
    missing: list[str] = []
    for key in required:
        if key not in output:
            missing.append(key)
            continue
        if isinstance(output[key], str) and not output[key].strip():
            missing.append(key)
    return [f"{role}:missing_required:{key}" for key in missing]


def _rust_like_tokens(text: str) -> list[str]:
    value = str(text)
    patterns = [
        r"\b[a-z_][a-z0-9_]*(?:::[a-z_][a-z0-9_]*)+\b",
        r"\b[A-Z][A-Za-z0-9_]*(?:::[A-Za-z0-9_]+)*\b",
        r"\b[a-z_][a-z0-9_]*\b",
    ]
    seen: set[str] = set()
    out: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, value):
            token = match.group(0).strip()
            key = token.lower()
            if not token or key in seen:
                continue
            seen.add(key)
            out.append(token)
    return out


def _synonym_alias_map(synonyms_cfg: dict[str, Any]) -> dict[str, str]:
    synonyms = synonyms_cfg.get("synonyms", {}) if isinstance(synonyms_cfg, dict) else {}
    if not isinstance(synonyms, dict):
        return {}
    alias_map: dict[str, str] = {}
    for canonical_raw, aliases_raw in synonyms.items():
        canonical = str(canonical_raw).strip()
        if not canonical:
            continue
        alias_map[canonical.lower()] = canonical
        aliases = aliases_raw if isinstance(aliases_raw, list) else []
        for alias in aliases:
            alias_text = str(alias).strip()
            if alias_text:
                alias_map[alias_text.lower()] = canonical
    return alias_map


def _normalize_construct_scope(
    raw_scope: Any,
    *,
    supplemental_text: list[str],
    alias_map: dict[str, str],
) -> tuple[list[str], list[str]]:
    patterns: list[str] = []
    raw_tokens: list[str] = []
    if isinstance(raw_scope, list):
        for item in raw_scope:
            token = str(item).strip()
            if token:
                raw_tokens.append(token)
        if raw_tokens:
            patterns.append("scope:list")
    elif isinstance(raw_scope, str) and raw_scope.strip():
        raw_tokens.extend(_rust_like_tokens(raw_scope))
        patterns.append("scope:string")
    if not raw_tokens:
        for text in supplemental_text:
            raw_tokens.extend(_rust_like_tokens(text))
        if raw_tokens:
            patterns.append("scope:supplemental")

    merged: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        token_clean = token.strip()
        token_key = token_clean.lower()
        if token_clean and token_key not in seen:
            seen.add(token_key)
            merged.append(token_clean)
        canonical = alias_map.get(token_key)
        if canonical:
            canonical_key = canonical.lower()
            if canonical_key not in seen:
                seen.add(canonical_key)
                merged.append(canonical)
    return merged, patterns


def _normalize_evidence_refs(raw_refs: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if isinstance(raw_refs, list):
        for entry in raw_refs:
            if isinstance(entry, dict):
                evidence_id = str(
                    entry.get("evidence_id", entry.get("chunk_id", entry.get("id", "")))
                ).strip()
                if not evidence_id:
                    continue
                refs.append(
                    {
                        "evidence_id": evidence_id,
                        "quote": str(entry.get("quote", "")).strip() or None,
                        "doc_id": str(entry.get("doc_id", "")).strip() or None,
                        "anchor": str(entry.get("anchor", "")).strip() or None,
                    }
                )
                continue
            evidence_id = str(entry).strip()
            if evidence_id:
                refs.append({"evidence_id": evidence_id})
    return refs


def _normalize_claim_map(
    raw_claim_map: Any, *, target_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    patterns: list[str] = []
    claims: list[dict[str, Any]] = []

    def _append_claim(claim_id_seed: str, claim_text_raw: Any, evidence_raw: Any) -> None:
        claim_text = str(claim_text_raw).strip()
        if not claim_text:
            return
        refs = _normalize_evidence_refs(evidence_raw)
        claim_id = str(claim_id_seed).strip()
        if not claim_id:
            claim_id = f"{target_id}::claim::{len(claims) + 1}"
        claims.append(
            {
                "claim_id": claim_id,
                "claim_text": claim_text,
                "evidence_refs": refs,
            }
        )

    if isinstance(raw_claim_map, list):
        patterns.append("claim_map:list")
        for idx, entry in enumerate(raw_claim_map, start=1):
            if not isinstance(entry, dict):
                continue
            claim_id = str(entry.get("claim_id", "")).strip() or f"{target_id}::claim::{idx}"
            claim_text = entry.get("claim_text", entry.get("claim", ""))
            evidence_raw = entry.get("evidence_refs", entry.get("evidence_ids", []))
            _append_claim(claim_id, claim_text, evidence_raw)
    elif isinstance(raw_claim_map, dict):
        dict_of_dicts = all(isinstance(v, dict) for v in raw_claim_map.values())
        if dict_of_dicts:
            patterns.append("claim_map:dict_of_dicts")
            for idx, (key, value) in enumerate(raw_claim_map.items(), start=1):
                nested = value if isinstance(value, dict) else {}
                claim_id = str(nested.get("claim_id", key)).strip() or f"{target_id}::claim::{idx}"
                claim_text = nested.get("claim_text", nested.get("claim", ""))
                evidence_raw = nested.get("evidence_refs", nested.get("evidence_ids", []))
                _append_claim(claim_id, claim_text, evidence_raw)
        else:
            patterns.append("claim_map:paired_dict")
            keys = [str(k) for k in raw_claim_map.keys()]
            for idx, key in enumerate(keys, start=1):
                if key.endswith("_evidence"):
                    continue
                value = raw_claim_map.get(key, "")
                if isinstance(value, list):
                    claim_text = key
                    evidence_raw = value
                else:
                    claim_text = value
                    evidence_raw = raw_claim_map.get(f"{key}_evidence", [])
                claim_id = key.strip() or f"{target_id}::claim::{idx}"
                _append_claim(claim_id, claim_text, evidence_raw)

    for idx, claim in enumerate(claims, start=1):
        claim_id = str(claim.get("claim_id", "")).strip()
        if not claim_id:
            claim["claim_id"] = f"{target_id}::claim::{idx}"
    return claims, patterns


def _resolve_fls_for_construct_safe(construct_terms: list[str]) -> dict[str, str]:
    if not construct_terms:
        return {"paragraph_id": "fls_UNRESOLVED", "unresolved_reason": "empty_construct_scope"}
    try:
        return resolve_fls_for_construct(construct_terms)
    except RuntimeError:
        return {
            "paragraph_id": "fls_UNRESOLVED",
            "unresolved_reason": "fls_db_unavailable",
        }


def _resolve_bibliography_rows(
    metadata_output: dict[str, Any],
    *,
    prompt_id: str,
    run_id: str,
    evidence_lookup: dict[str, dict[str, Any]],
    evidence_ids: list[str],
    construct_terms: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    patterns: list[str] = []
    raw_rows = metadata_output.get("bibliography_rows") if isinstance(metadata_output, dict) else []
    rows: list[dict[str, Any]] = []
    if isinstance(raw_rows, list):
        for row in raw_rows:
            if isinstance(row, dict):
                rows.append(dict(row))
    if rows:
        patterns.append("bibliography:from_writer")
    else:
        patterns.append("bibliography:synthetic")
        for idx, evidence_id in enumerate(evidence_ids, start=1):
            lookup = evidence_lookup.get(evidence_id, {})
            rows.append(
                {
                    "citation_key": f"{prompt_id}:SRC-{idx}",
                    "evidence_id": evidence_id,
                    "source": str(lookup.get("source", "calibration_evidence_bundle")),
                    "locator": {
                        "path": f"evidence_bundle/calibration_evidence.json",
                        "anchor": str(lookup.get("anchor", evidence_id)),
                    },
                    "excerpt": str(lookup.get("text", ""))[:280],
                    "run_id": run_id,
                }
            )

    resolved: list[dict[str, Any]] = []
    fls_info = _resolve_fls_for_construct_safe(construct_terms or [])
    fls_id = str(fls_info.get("paragraph_id", "fls_UNRESOLVED"))
    for idx, row in enumerate(rows, start=1):
        citation_key = str(row.get("citation_key", "")).strip() or f"{prompt_id}:SRC-{idx}"
        evidence_id = str(row.get("evidence_id", "")).strip()
        lookup = evidence_lookup.get(evidence_id, {}) if evidence_id else {}
        source = str(row.get("source", lookup.get("source", ""))).strip()
        locator_raw = row.get("locator")
        locator = locator_raw if isinstance(locator_raw, dict) else {}
        if not (
            str(locator.get("url", "")).strip()
            or str(locator.get("path", "")).strip()
            or str(locator.get("paragraph_id", "")).strip()
        ):
            if fls_id and fls_id != "fls_UNRESOLVED":
                locator = {
                    "paragraph_id": fls_id,
                    "resolution_source": "fls_spec_db",
                }
                patterns.append("bibliography:fls_spec_lookup")
            else:
                locator = {
                    "path": "evidence_bundle/calibration_evidence.json",
                    "anchor": str(lookup.get("anchor", evidence_id or citation_key)),
                }
        if not source:
            source = str(lookup.get("source", "calibration_evidence_bundle"))
        resolved.append(
            {
                "target_id": str(row.get("target_id", "")).strip()
                or str(metadata_output.get("target_id", "")),
                "prompt_id": prompt_id,
                "citation_key": citation_key,
                "evidence_id": evidence_id or None,
                "source": source,
                "locator": locator,
                "excerpt": str(row.get("excerpt", lookup.get("text", "")))[:320],
            }
        )
    return resolved, patterns


def _call_opencode_cli(
    *,
    role: str,
    prompt: str,
    model: str,
    temperature: float,
    timeout_s: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _ = temperature
    request_started_at = datetime.now(UTC).isoformat()
    request_id = f"sysreq::{uuid.uuid4().hex[:20]}"
    system_prompt = (
        f"You are {role}. Output one valid JSON object only. "
        "Do not include markdown fences or explanatory text."
    )
    command = ["opencode", "run", "--format", "json", "--agent", "plan"]
    model_value = model.strip()
    if model_value:
        command.extend(["--model", model_value])
    command.append(f"{system_prompt}\n\n{prompt}")
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        if completed.returncode != 0:
            stderr_text = completed.stderr.strip()
            raise RuntimeError(
                f"opencode_run_failed: rc={completed.returncode} stderr={stderr_text}"
            )
        content_parts: list[str] = []
        provider_message_id: str | None = None
        provider_token_usage: dict[str, Any] | None = None
        for line in completed.stdout.splitlines():
            raw_line = line.strip()
            if not raw_line:
                continue
            event = json.loads(raw_line)
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type", ""))
            if event_type == "text":
                part_raw = event.get("part")
                part: dict[str, Any] = part_raw if isinstance(part_raw, dict) else {}
                text = str(part.get("text", ""))
                if text:
                    content_parts.append(text)
                metadata_raw = part.get("metadata")
                metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
                openai_meta_raw = metadata.get("openai")
                openai_meta: dict[str, Any] = (
                    openai_meta_raw if isinstance(openai_meta_raw, dict) else {}
                )
                item_id = str(openai_meta.get("itemId", "")).strip()
                if item_id:
                    provider_message_id = item_id
            if event_type == "step_finish":
                part_raw = event.get("part")
                part: dict[str, Any] = part_raw if isinstance(part_raw, dict) else {}
                tokens = part.get("tokens")
                if isinstance(tokens, dict):
                    provider_token_usage = tokens
        content = "\n".join(content_parts).strip()
        if not content.strip():
            raise RuntimeError("LLM response was empty")
        output = _extract_json_object(content)
        response_received_at = datetime.now(UTC).isoformat()
        invocation = {
            "system_request_id": request_id,
            "request_started_at": request_started_at,
            "response_received_at": response_received_at,
            "prompt_digest": _canonical_digest(prompt),
            "response_digest": _canonical_digest(content),
            "transport_status": "ok",
            "provider_model": model_value or "opencode/default",
            "provider_message_id": provider_message_id,
            "provider_token_usage": provider_token_usage,
            "transport_backend": "opencode_cli",
        }
        return output, invocation
    except (
        subprocess.SubprocessError,
        TimeoutError,
        json.JSONDecodeError,
        ValueError,
        RuntimeError,
    ) as exc:
        response_received_at = datetime.now(UTC).isoformat()
        invocation = {
            "system_request_id": request_id,
            "request_started_at": request_started_at,
            "response_received_at": response_received_at,
            "prompt_digest": _canonical_digest(prompt),
            "response_digest": "",
            "transport_status": f"error:{type(exc).__name__}",
            "error": str(exc),
            "provider_model": model_value or "opencode/default",
            "transport_backend": "opencode_cli",
        }
        raise RuntimeError(json.dumps(invocation, sort_keys=True)) from exc


def _load_retry_depth_policy(root: Path, run_dir: Path) -> tuple[str, int, float | None]:
    candidates = [
        run_dir / "retry_pilot_results.json",
        root / ".cache" / "retry_pilot_results.json",
        root / "retry_pilot_results.json",
    ]
    payload: dict[str, Any] = {}
    for path in candidates:
        if path.exists():
            payload = _read_json(path)
            break

    rate_raw = payload.get("first_retry_resolution_rate") if isinstance(payload, dict) else None
    rate: float | None = None
    if isinstance(rate_raw, (int, float)):
        rate = float(rate_raw)

    if rate is None:
        return "viable", 2, None
    if rate >= 0.50:
        return "viable", 2, rate
    if rate >= 0.25:
        return "marginal", 1, rate
    return "not-viable", 0, rate


def run_scaffold_s0_config(args: Namespace, *, root: Path) -> int:
    from retrieval.services.phase_a_scaffold import run_scaffold_s0_config as _delegate

    return _delegate(args, root=root)


def run_doctor(args: Namespace, *, root: Path) -> int:
    from retrieval.services.phase_a_doctor import run_doctor as _delegate

    return _delegate(args, root=root)


def run_enumerate_targets(args: Namespace, *, root: Path) -> int:
    from retrieval.services.phase_a_targets import run_enumerate_targets as _delegate

    return _delegate(args, root=root)


def _run_eval_for_corpus(
    root: Path,
    db_path: Path,
    corpus: str,
    run_dir: Path,
    *,
    semantic: bool = False,
) -> tuple[dict[str, Any], list[str], Path]:
    from retrieval.services.phase_a_targets import _run_eval_for_corpus as _delegate

    return _delegate(root, db_path, corpus, run_dir, semantic=semantic)


def run_calibration_run(args: Namespace, *, root: Path) -> int:
    from retrieval.services.phase_a_calibration import run_calibration_run as _delegate

    return _delegate(args, root=root)


def run_enforce_calibration_quality(args: Namespace, *, root: Path) -> int:
    return _run_enforce_calibration_quality(args, root=root)


def run_pack_reviewer_packet(args: Namespace, *, root: Path) -> int:
    return _run_pack_reviewer_packet(args, root=root)
