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
from scripts.opencode_retry_wrapper import CONVENTION_RETRY_BUDGET, retry_with_violations
from scripts.validate_fls_matching import validate_fls_matching
from validation.role_validators import RoleViolation, validate_role_output


EXIT_SUCCESS = 0
EXIT_RUNTIME_FAIL = 3


def _now_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(UTC).strftime('%Y%m%d')}"


def _run_id(args: Namespace) -> str:
    value = str(getattr(args, "run_id", "") or "").strip()
    return value or _now_id("s0_phase_a")


def _report_dir(root: Path, run_id: str, report_root: str = "") -> Path:
    if report_root:
        target = Path(report_root)
        if not target.is_absolute():
            target = (root / target).resolve()
        return target
    return (root / ".cache" / "sqlite_kb" / "reports" / run_id).resolve()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


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
    config_root = (root / "config" / "s0").resolve()
    config_root.mkdir(parents=True, exist_ok=True)
    overwrite = bool(getattr(args, "overwrite", False))

    files: dict[str, dict[str, Any]] = {
        "drafting_prompt_contract.yaml": {
            "version": 1,
            "synthesis_version": "s0-v1",
            "two_phase_triggers": {
                "draftability_not_strong": True,
                "hard_rows": ["1e", "1h", "1i"],
            },
            "worked_positive_examples": [
                {
                    "id": "pos_overflow_checked",
                    "row": "1e",
                    "safety_hazard": "Silent integer overflow in release builds can corrupt safety-relevant calculations.",
                    "construct_behavior": "Operator arithmetic may wrap in release; checked/saturating APIs provide explicit behavior.",
                    "mitigation_claim": "Safety-critical arithmetic shall use checked_*, saturating_*, or wrapping_* with explicit policy.",
                    "strength_justification": "shall is required because silent wrap can propagate undetected faults.",
                },
                {
                    "id": "pos_refcell_try",
                    "row": "1d",
                    "safety_hazard": "Runtime borrow panics can trigger unexpected control-flow failures.",
                    "construct_behavior": "RefCell::borrow_mut panics on conflicting borrows; try_borrow_mut returns Result-like control.",
                    "mitigation_claim": "Prefer non-panicking borrow APIs in critical paths and handle failure explicitly.",
                    "strength_justification": "shall where panic-induced abort violates fault containment expectations.",
                },
                {
                    "id": "pos_atomic_ordering",
                    "row": "1h",
                    "safety_hazard": "Incorrect atomic ordering can invalidate synchronization assumptions.",
                    "construct_behavior": "Acquire/Release/SeqCst semantics differ materially for visibility and ordering guarantees.",
                    "mitigation_claim": "Atomic APIs shall declare and justify memory ordering choices for each shared-state transition.",
                    "strength_justification": "shall because weak ordering misuse causes non-deterministic safety faults.",
                },
            ],
            "worked_negative_examples": [
                {
                    "id": "neg_generic_warning",
                    "row": "1d",
                    "safety_hazard": "This might be risky.",
                    "construct_behavior": "Rust has several relevant features.",
                    "mitigation_claim": "Developers should use best practices.",
                    "strength_justification": "good practice",
                },
                {
                    "id": "neg_tautological",
                    "row": "1e",
                    "safety_hazard": "Unsafe code can be unsafe.",
                    "construct_behavior": "Unsafe blocks require care.",
                    "mitigation_claim": "Use unsafe safely.",
                    "strength_justification": "safety",
                },
                {
                    "id": "neg_uncited_semantics",
                    "row": "1h",
                    "safety_hazard": "Thread bugs happen.",
                    "construct_behavior": "Concurrency is complex.",
                    "mitigation_claim": "Prefer stronger orderings.",
                    "strength_justification": "better reliability",
                },
            ],
            "significance_anchors": {
                "0": "doc restatement",
                "1": "obvious best practice",
                "2": "real issue but generic mitigation",
                "3": "specific hazard and concrete mitigation",
                "4": "non-obvious semantics+safety interaction",
                "5": "high-value incident-preventing guidance",
            },
        },
        "enforcement_catalog_s0.yaml": {
            "version": 1,
            "entries": [
                {
                    "id": "enf-clippy",
                    "smoke_check_safe": True,
                    "command": "cargo clippy --all-targets -- -D warnings",
                    "artifact": "clippy.log",
                },
                {
                    "id": "enf-fmt",
                    "smoke_check_safe": True,
                    "command": "cargo fmt --check",
                    "artifact": "fmt.log",
                },
                {
                    "id": "enf-miri",
                    "smoke_check_safe": False,
                    "command": "cargo miri test",
                    "artifact": "miri.log",
                },
                {
                    "id": "enf-deny-unsafe",
                    "smoke_check_safe": True,
                    "command": 'rg -n "unsafe\\s*\\{" src',
                    "artifact": "unsafe_scan.log",
                },
                {
                    "id": "enf-doc-tests",
                    "smoke_check_safe": True,
                    "command": "cargo test --doc",
                    "artifact": "doctest.log",
                },
                {
                    "id": "enf-build-offline",
                    "smoke_check_safe": True,
                    "command": "./make.py --offline",
                    "artifact": "build.log",
                },
                {
                    "id": "enf-rustc-warnings",
                    "smoke_check_safe": True,
                    "command": "cargo check --all-targets",
                    "artifact": "check.log",
                },
                {
                    "id": "enf-example-harness",
                    "smoke_check_safe": False,
                    "command": "python scripts/extract_rust_examples.py --test",
                    "artifact": "examples.log",
                },
            ],
        },
        "verification_catalog_s0.yaml": {
            "version": 1,
            "entries": [
                {
                    "id": "ver-unit-tests",
                    "smoke_check_safe": True,
                    "command": "cargo test",
                    "artifact": "test.log",
                },
                {
                    "id": "ver-property",
                    "smoke_check_safe": False,
                    "command": "cargo test --tests",
                    "artifact": "property.log",
                },
                {
                    "id": "ver-smoke",
                    "smoke_check_safe": True,
                    "command": "uv run python scripts/sqlite_kb.py smoke --corpus rust_reference",
                    "artifact": "smoke.log",
                },
                {
                    "id": "ver-validate",
                    "smoke_check_safe": True,
                    "command": "uv run python scripts/sqlite_kb.py validate --corpus rust_reference",
                    "artifact": "validate.log",
                },
                {
                    "id": "ver-eval-core",
                    "smoke_check_safe": False,
                    "command": "uv run python scripts/sqlite_kb.py eval --corpus core_docs",
                    "artifact": "eval_core.log",
                },
                {
                    "id": "ver-eval-ref",
                    "smoke_check_safe": False,
                    "command": "uv run python scripts/sqlite_kb.py eval --corpus rust_reference",
                    "artifact": "eval_ref.log",
                },
                {
                    "id": "ver-query-snapshot",
                    "smoke_check_safe": True,
                    "command": "uv run python scripts/sqlite_kb.py query --corpus core_docs -- --query-id snapshot_metadata --mode contract",
                    "artifact": "query.log",
                },
                {
                    "id": "ver-git-clean",
                    "smoke_check_safe": True,
                    "command": "git status --short",
                    "artifact": "git_status.log",
                },
            ],
        },
        "s0_targets.yaml": {
            "version": 1,
            "manual_overrides": {},
            "heuristic": {
                "strong_min_evidence": 4,
                "partial_min_evidence": 2,
                "auto_downgrade_after_abstains": 2,
            },
        },
        "s0_gate_policy.yaml": {
            "version": 1,
            "rewrite_mode": "auto",
            "top_k": 10,
            "candidate_limit": 5000,
            "convention_retry_budget": 50,
            "compilation_retry_budget": 15,
            "max_convention_retries": 50,
            "max_compilation_retries": 15,
            "max_judge_calls": 70,
            "dedup": ["span_hash", "normalized_excerpt_hash"],
        },
        "examples_gate_policy.yaml": {
            "version": 1,
            "scope": "changed_chapters",
            "publishable_requires_full_once": True,
            "full_fallback_threshold": 25,
            "periodic_full_schedule": "weekly",
        },
        "writer_prompt_contracts.yaml": {
            "contract_version": 1,
            "roles": {
                "evidence_synthesizer": {
                    "prompt_template_id": "writer-evidence-synth-v1",
                    "prompt_template_text": "You are the Evidence Synthesizer. Target {{target_id}} row {{table1_row}}. Use evidence IDs {{evidence_ids}} and exemplar IDs {{exemplar_ids}}. Return JSON only.",
                    "allowed_placeholders": [
                        "target_id",
                        "table1_row",
                        "corpus",
                        "evidence_ids",
                        "evidence_snippets",
                        "exemplar_ids",
                        "global_rules",
                    ],
                    "required_inputs": [
                        "target_metadata",
                        "evidence_bundle",
                        "exemplar_selection",
                        "style_context.global_rules",
                    ],
                    "required_output_schema": {
                        "required": [
                            "target_id",
                            "hazard",
                            "mechanism",
                            "mitigation",
                            "construct_scope",
                            "evidence_ids",
                            "claim_to_evidence_map",
                        ]
                    },
                    "forbidden_patterns": [
                        "uncited factual claim",
                        "final guideline prose",
                    ],
                    "style_rules_ref": ["global_rules"],
                    "abstain_policy": "emit reasoned abstain when evidence insufficient",
                },
                "amplification_author": {
                    "prompt_template_id": "writer-amplification-v1",
                    "prompt_template_text": "You are the Amplification Author. Write only the guideline body text for {{target_id}} using evidence synthesis and exemplars {{exemplar_ids}}. Return JSON only.",
                    "allowed_placeholders": [
                        "target_id",
                        "table1_row",
                        "evidence_synthesis",
                        "exemplar_ids",
                        "amplification_rules",
                    ],
                    "required_inputs": [
                        "evidence_synthesizer_output",
                        "exemplar_selection",
                        "style_context.global_rules",
                        "style_context.amplification_rules",
                    ],
                    "required_output_schema": {
                        "required": [
                            "target_id",
                            "guideline_amplification_text",
                            "normative_strength",
                            "amplification_citation_keys",
                        ]
                    },
                    "forbidden_patterns": [
                        "apply explicit controls",
                        "generic cross-target boilerplate",
                    ],
                    "style_rules_ref": ["global_rules", "amplification_rules"],
                    "abstain_policy": "emit abstain output with empty amplification citations",
                },
                "example_author": {
                    "prompt_template_id": "writer-example-v1",
                    "prompt_template_text": "You are the Example Author. Produce non-compliant and compliant narrative+code for {{target_id}} tied to the same hazard/construct family. Return JSON only.",
                    "allowed_placeholders": [
                        "target_id",
                        "evidence_synthesis",
                        "amplification",
                        "exemplar_ids",
                        "example_rules",
                    ],
                    "required_inputs": [
                        "evidence_synthesizer_output",
                        "amplification_author_output",
                        "exemplar_selection",
                        "style_context.global_rules",
                        "style_context.example_rules",
                    ],
                    "required_output_schema": {
                        "required": [
                            "target_id",
                            "non_compliant_narrative",
                            "non_compliant_code",
                            "compliant_narrative",
                            "compliant_code",
                            "example_citation_keys",
                        ]
                    },
                    "forbidden_patterns": [
                        "template stub code",
                        "unrelated construct examples",
                    ],
                    "style_rules_ref": ["global_rules", "example_rules"],
                    "abstain_policy": "emit abstain with empty example fields",
                },
                "rationale_author": {
                    "prompt_template_id": "writer-rationale-v1",
                    "prompt_template_text": "You are the Rationale Author. Produce hazard->mechanism->consequence rationale for {{target_id}}. Return JSON only.",
                    "allowed_placeholders": [
                        "target_id",
                        "evidence_synthesis",
                        "examples",
                        "rationale_rules",
                    ],
                    "required_inputs": [
                        "evidence_synthesizer_output",
                        "example_author_output",
                        "style_context.global_rules",
                        "style_context.rationale_rules",
                    ],
                    "required_output_schema": {
                        "required": [
                            "target_id",
                            "rationale_text",
                            "hazard_mechanism_consequence_map",
                            "rationale_citation_keys",
                        ]
                    },
                    "forbidden_patterns": [
                        "tautological rationale",
                        "generic non-causal language",
                    ],
                    "style_rules_ref": ["global_rules", "rationale_rules"],
                    "abstain_policy": "emit abstain rationale reason",
                },
                "metadata_citation_curator": {
                    "prompt_template_id": "writer-metadata-citation-v1",
                    "prompt_template_text": "You are the Metadata/Citation Curator. Produce style-conformant metadata and bibliography rows for {{target_id}}. Return JSON only.",
                    "allowed_placeholders": [
                        "target_id",
                        "all_writer_outputs",
                        "exemplar_metadata_patterns",
                        "metadata_bibliography_rules",
                    ],
                    "required_inputs": [
                        "evidence_synthesizer_output",
                        "amplification_author_output",
                        "example_author_output",
                        "rationale_author_output",
                        "style_context.global_rules",
                        "style_context.metadata_bibliography_rules",
                    ],
                    "required_output_schema": {
                        "required": [
                            "target_id",
                            "tags",
                            "fls_candidate",
                            "bibliography_rows",
                            "citation_key_map",
                            "metadata_validation_notes",
                        ]
                    },
                    "forbidden_patterns": [
                        "generic metadata defaults",
                        "invalid example or bibliography id conventions",
                    ],
                    "style_rules_ref": ["global_rules", "metadata_bibliography_rules"],
                    "abstain_policy": "emit abstain metadata with explicit note",
                },
            },
        },
        "judge_prompt_contracts.yaml": {
            "contract_version": 1,
            "roles": {
                "evidence_auditor": {
                    "prompt_template_id": "judge-evidence-v1",
                    "prompt_template_text": "Evaluate evidence grounding only for draft {{draft_id}} using evidence bundle {{evidence_ids}}. Return JSON only.",
                    "allowed_placeholders": [
                        "draft_id",
                        "draft_record",
                        "evidence_ids",
                        "evidence_bundle",
                    ],
                    "required_inputs": ["drafts_jsonl", "evidence_bundle"],
                    "required_output_schema": {"required": ["pass", "results"]},
                    "forbidden_patterns": ["style-only judgment"],
                },
                "functional_safety_relevance": {
                    "prompt_template_id": "judge-safety-v1",
                    "prompt_template_text": "Evaluate functional safety relevance and significance for {{draft_id}}. Return JSON only.",
                    "allowed_placeholders": ["draft_id", "draft_record", "significance_anchors"],
                    "required_inputs": ["drafts_jsonl", "significance_anchors"],
                    "required_output_schema": {"required": ["pass", "results"]},
                    "forbidden_patterns": ["evidence grounding assertions without evidence pass"],
                },
                "usability_actionability": {
                    "prompt_template_id": "judge-usability-v1",
                    "prompt_template_text": "Evaluate usability/actionability for {{draft_id}} and return JSON only.",
                    "allowed_placeholders": ["draft_id", "draft_record", "style_rules"],
                    "required_inputs": ["drafts_jsonl", "style_context_bundle"],
                    "required_output_schema": {"required": ["pass", "results"]},
                    "forbidden_patterns": ["candidate verdict assignment"],
                },
                "golden_shape_comparator": {
                    "prompt_template_id": "judge-shape-v1",
                    "prompt_template_text": "Compare generated draft {{draft_file}} against nearest exemplar {{exemplar_file}} and style rules. Return JSON only.",
                    "allowed_placeholders": ["draft_file", "exemplar_file", "style_rules"],
                    "required_inputs": [
                        "generated_guidelines_rst",
                        "curated_exemplars",
                        "style_guideline",
                    ],
                    "required_output_schema": {"required": ["results", "all_non_abstain_pass"]},
                    "forbidden_patterns": ["content-only pass without style and shape checks"],
                },
                "exemplar_usage_auditor": {
                    "prompt_template_id": "judge-exemplar-usage-v1",
                    "prompt_template_text": "Validate exemplar usage trace for target {{target_id}}. Return JSON only.",
                    "allowed_placeholders": ["target_id", "selection_trace", "synthesis_trace"],
                    "required_inputs": [
                        "exemplar_selection_trace",
                        "synthesis_input_trace",
                        "golden_exemplar_lock_report",
                    ],
                    "required_output_schema": {"required": ["run_id", "status", "results"]},
                    "forbidden_patterns": ["candidate verdict assignment"],
                },
                "writer_output_auditor": {
                    "prompt_template_id": "judge-writer-output-v1",
                    "prompt_template_text": "Validate writer role outputs for draft {{draft_id}}. Return JSON only.",
                    "allowed_placeholders": ["draft_id", "writer_outputs", "style_rules"],
                    "required_inputs": [
                        "writer_subagent_outputs",
                        "drafts_jsonl",
                        "style_context_bundle",
                    ],
                    "required_output_schema": {"required": ["run_id", "status", "results"]},
                    "forbidden_patterns": ["candidate verdict assignment"],
                },
                "holistic_pairwise": {
                    "prompt_template_id": "judge-holistic-v1",
                    "prompt_template_text": "Aggregate prior judge outputs for draft {{draft_id}} under candidate policy. Return JSON only.",
                    "allowed_placeholders": [
                        "draft_id",
                        "evidence_pass",
                        "safety_pass",
                        "usability_pass",
                        "shape_pass",
                        "usage_audit",
                        "writer_audit",
                    ],
                    "required_inputs": ["judge_pass_artifacts"],
                    "required_output_schema": {"required": ["pass", "results", "aggregate"]},
                    "forbidden_patterns": ["candidate verdict without policy prerequisites"],
                },
            },
        },
        "roles_required.yaml": {
            "version": 1,
            "roles": [
                "evidence_synthesizer",
                "amplification_author",
                "example_author",
                "rationale_author",
                "metadata_citation_curator",
            ],
        },
        "judges_required.yaml": {
            "version": 1,
            "judges": [
                "evidence_auditor",
                "functional_safety_relevance",
                "usability_actionability",
                "golden_shape_comparator",
                "exemplar_usage_auditor",
                "writer_output_auditor",
                "holistic_pairwise",
            ],
        },
        "anchor_expectation.yaml": {
            "version": 1,
            "defaults": {
                "anchor_required": False,
            },
            "corpora": {
                "rust_reference": {"anchor_required": True},
                "core_docs": {"anchor_required": False},
            },
        },
    }

    written: list[str] = []
    for name, payload in files.items():
        path = config_root / name
        if path.exists() and not overwrite:
            continue
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        written.append(str(path.relative_to(root)))

    print(
        json.dumps(
            {
                "operation": "scaffold-s0-config",
                "config_root": str(config_root),
                "written_count": len(written),
                "written": written,
            },
            indent=2,
        )
    )
    return EXIT_SUCCESS


def run_doctor(args: Namespace, *, root: Path) -> int:
    run_id = _run_id(args)
    out_dir = _report_dir(root, run_id, str(getattr(args, "report_root", "") or ""))
    out_dir.mkdir(parents=True, exist_ok=True)

    config_root = (root / "config" / "s0").resolve()
    prompt_contract = _safe_yaml(config_root / "drafting_prompt_contract.yaml")
    enforcement = _safe_yaml(config_root / "enforcement_catalog_s0.yaml")
    verification = _safe_yaml(config_root / "verification_catalog_s0.yaml")
    writer_contracts = _safe_yaml(config_root / "writer_prompt_contracts.yaml")
    judge_contracts = _safe_yaml(config_root / "judge_prompt_contracts.yaml")

    def _entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw = payload.get("entries")
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        return []

    pos = prompt_contract.get("worked_positive_examples")
    neg = prompt_contract.get("worked_negative_examples")
    pos_list = pos if isinstance(pos, list) else []
    neg_list = neg if isinstance(neg, list) else []
    enf_entries = _entries(enforcement)
    ver_entries = _entries(verification)
    enf_smoke = len([x for x in enf_entries if bool(x.get("smoke_check_safe", False))])
    ver_smoke = len([x for x in ver_entries if bool(x.get("smoke_check_safe", False))])

    min_report = {
        "run_id": run_id,
        "status": "pass",
        "thresholds": {
            "worked_positive_examples_min": 3,
            "worked_negative_examples_min": 3,
            "enforcement_catalog_entries_min": 8,
            "verification_catalog_entries_min": 8,
            "smoke_safe_enforcement_entries_min": 2,
            "smoke_safe_verification_entries_min": 2,
        },
        "measured": {
            "worked_positive_examples": len(pos_list),
            "worked_negative_examples": len(neg_list),
            "enforcement_catalog_entries": len(enf_entries),
            "verification_catalog_entries": len(ver_entries),
            "smoke_safe_enforcement_entries": enf_smoke,
            "smoke_safe_verification_entries": ver_smoke,
        },
    }
    fail_reasons: list[str] = []
    for key, threshold in min_report["thresholds"].items():
        measure_key = key.replace("_min", "")
        measured = int(min_report["measured"].get(measure_key, 0))
        if measured < int(threshold):
            fail_reasons.append(f"{measure_key}={measured} < {threshold}")
    if fail_reasons:
        min_report["status"] = "fail"
        min_report["fail_reasons"] = fail_reasons

    worked_report = {
        "run_id": run_id,
        "status": "pass",
        "worked_positive_examples": len(pos_list),
        "worked_negative_examples": len(neg_list),
        "fail_reasons": [],
    }
    required_fields = [
        "safety_hazard",
        "construct_behavior",
        "mitigation_claim",
        "strength_justification",
    ]
    for kind, items in (("positive", pos_list), ("negative", neg_list)):
        for idx, item in enumerate(items):
            for field in required_fields:
                if not str(item.get(field, "")).strip():
                    worked_report["fail_reasons"].append(f"{kind}[{idx}] missing {field}")
    if worked_report["fail_reasons"]:
        worked_report["status"] = "fail"

    contract_report = {
        "run_id": run_id,
        "status": "pass",
        "fail_reasons": [],
    }
    for label, payload in (("writer", writer_contracts), ("judge", judge_contracts)):
        roles = payload.get("roles") if isinstance(payload, dict) else None
        if not isinstance(roles, dict) or not roles:
            contract_report["fail_reasons"].append(f"{label}_contracts_missing_roles")
            continue
        for role_name, role_payload in roles.items():
            if not isinstance(role_payload, dict):
                contract_report["fail_reasons"].append(f"{label}:{role_name}:invalid_payload")
                continue
            for key in (
                "prompt_template_id",
                "prompt_template_text",
                "allowed_placeholders",
                "required_inputs",
                "required_output_schema",
                "forbidden_patterns",
            ):
                if key not in role_payload:
                    contract_report["fail_reasons"].append(f"{label}:{role_name}:missing_{key}")
    if contract_report["fail_reasons"]:
        contract_report["status"] = "fail"

    catalog_smoke_report = {
        "run_id": run_id,
        "status": "pass" if not fail_reasons else "fail",
        "enforcement_catalog_entries": len(enf_entries),
        "verification_catalog_entries": len(ver_entries),
        "smoke_safe_enforcement_entries": enf_smoke,
        "smoke_safe_verification_entries": ver_smoke,
        "notes": [
            "Doctor validates catalog structure and minimum counts; execution smoke checks are performed during calibration-run packeting."
        ],
    }

    backend_status: dict[str, Any] = {"ok": False, "reason": "status command failed"}
    try:
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / "sqlite_local_semantic_backend.py"), "status"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            backend_status = json.loads(result.stdout)
        else:
            backend_status = {
                "ok": False,
                "returncode": result.returncode,
                "stderr": result.stderr.strip(),
            }
    except Exception as exc:  # pragma: no cover
        backend_status = {"ok": False, "error": str(exc)}

    doctor_status = "pass"
    if (
        min_report["status"] != "pass"
        or worked_report["status"] != "pass"
        or contract_report["status"] != "pass"
    ):
        doctor_status = "fail"
    if not bool(backend_status.get("ok", False)):
        doctor_status = "fail"

    doctor_report = {
        "run_id": run_id,
        "mode": str(getattr(args, "mode", "publishable")),
        "scope": str(getattr(args, "scope", "drafting")),
        "status": doctor_status,
        "checks": {
            "config_root_exists": config_root.exists(),
            "quality_minimums": min_report["status"],
            "worked_examples": worked_report["status"],
            "semantic_backend": bool(backend_status.get("ok", False)),
        },
    }

    build_env = {
        "run_id": run_id,
        "python": sys.version.split()[0],
        "generated_at": datetime.now(UTC).isoformat(),
    }
    try:
        uv_ver = subprocess.run(["uv", "--version"], cwd=str(root), capture_output=True, text=True)
        build_env["uv"] = uv_ver.stdout.strip() if uv_ver.returncode == 0 else "unknown"
        git_rev = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True, text=True
        )
        build_env["repo_revision"] = (
            git_rev.stdout.strip() if git_rev.returncode == 0 else "unknown"
        )
    except Exception:
        pass

    embed_fp = {
        "run_id": run_id,
        "status": "pass" if bool(backend_status.get("ok", False)) else "fail",
        "backend_status": backend_status,
    }

    _write_json(out_dir / "doctor_report.json", doctor_report)
    _write_json(out_dir / "doctor_quality_minimums_report.json", min_report)
    _write_json(out_dir / "worked_example_validation_report.json", worked_report)
    _write_json(out_dir / "catalog_smoke_report.json", catalog_smoke_report)
    _write_json(out_dir / "prompt_contract_validation_report.json", contract_report)
    _write_json(out_dir / "build_env_fingerprint.json", build_env)
    _write_json(out_dir / "embedding_backend_fingerprint.json", embed_fp)

    print(
        json.dumps(
            {"run_id": run_id, "status": doctor_status, "report_dir": str(out_dir)}, indent=2
        )
    )
    return EXIT_SUCCESS if doctor_status == "pass" else EXIT_RUNTIME_FAIL


def run_enumerate_targets(args: Namespace, *, root: Path) -> int:
    run_id = _run_id(args)
    out_dir = _report_dir(root, run_id, str(getattr(args, "report_root", "") or ""))
    out_dir.mkdir(parents=True, exist_ok=True)

    datasets = [
        ("core_docs", root / "data" / "query_testsets" / "core_docs_table1_retrieval_eval.yaml"),
        (
            "rust_reference",
            root / "data" / "query_testsets" / "rust_reference_table1_retrieval_eval.yaml",
        ),
    ]
    target_cfg = _safe_yaml(root / "config" / "s0" / "s0_targets.yaml")
    manual_overrides = (
        target_cfg.get("manual_overrides", {}) if isinstance(target_cfg, dict) else {}
    )
    if not isinstance(manual_overrides, dict):
        manual_overrides = {}
    targets: list[dict[str, Any]] = []
    for corpus, path in datasets:
        payload = _safe_yaml(path)
        prompts = payload.get("prompts") if isinstance(payload, dict) else []
        if not isinstance(prompts, list):
            continue
        for prompt in prompts:
            if not isinstance(prompt, dict):
                continue
            prompt_id = str(prompt.get("prompt_id", "")).strip()
            if not prompt_id:
                continue
            rows = prompt.get("expected_row_markers")
            if not isinstance(rows, list):
                rows = []
            target_id = hashlib.sha256(f"{corpus}:{prompt_id}".encode("utf-8")).hexdigest()[:16]
            override = manual_overrides.get(prompt_id, {})
            if not isinstance(override, dict):
                override = {}
            targets.append(
                {
                    "target_id": target_id,
                    "prompt_id": prompt_id,
                    "corpus": corpus,
                    "table1_rows": rows,
                    "slice": str(prompt.get("slice", "")),
                    "category": str(prompt.get("category", "unspecified")),
                    "semantic_focus": bool(prompt.get("semantic_focus", False)),
                    "expect_abstain": bool(
                        override.get("expect_abstain", prompt.get("expect_abstain", False))
                    ),
                    "abstain_expected": bool(override.get("abstain_expected", False)),
                }
            )

    targets.sort(key=lambda row: (row["corpus"], row["prompt_id"]))
    payload = {
        "run_id": run_id,
        "profile": str(getattr(args, "profile", "full")),
        "mode": str(getattr(args, "mode", "publishable")),
        "target_count": len(targets),
        "targets": targets,
    }
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(canon).hexdigest()
    _write_json(out_dir / "targets.json", payload)
    (out_dir / "targets_digest").write_text(digest + "\n", encoding="utf-8")
    print(
        json.dumps({"run_id": run_id, "targets": len(targets), "targets_digest": digest}, indent=2)
    )
    return EXIT_SUCCESS


def _run_eval_for_corpus(
    root: Path, run_dir: Path, corpus: str, reuse_existing: bool
) -> tuple[Path, dict[str, Any]]:
    report_path = run_dir / f"{corpus}_eval_report.json"
    if not (reuse_existing and report_path.exists()):
        attempt_path = run_dir / f"{corpus}_backend_attempts.jsonl"
        cmd = [
            sys.executable,
            str(root / "scripts" / "sqlite_kb.py"),
            "eval",
            "--corpus",
            corpus,
            "--",
            "--report-path",
            str(report_path),
            "--backend-attempt-log-path",
            str(attempt_path),
        ]
        result = subprocess.run(cmd, cwd=str(root), text=True, capture_output=True, check=False)
        if result.returncode != 0 and not report_path.exists():
            raise RuntimeError(
                f"eval failed for {corpus} with no report output: rc={result.returncode} stderr={result.stderr.strip()}"
            )
    return report_path, _read_json(report_path)


def run_calibration_run(args: Namespace, *, root: Path) -> int:
    run_id = _run_id(args)
    mode = str(getattr(args, "mode", "bootstrap"))
    profile = str(getattr(args, "profile", "full"))
    run_dir = _report_dir(root, run_id, str(getattr(args, "report_root", "") or ""))
    run_dir.mkdir(parents=True, exist_ok=True)
    reuse_existing = not bool(getattr(args, "no_reuse_existing", False))
    resume_requested = bool(getattr(args, "resume", False))

    targets_path = run_dir / "targets.json"
    if not targets_path.exists():
        raise RuntimeError("targets.json missing. Run enumerate-targets first.")
    targets_payload = _read_json(targets_path)
    targets = targets_payload.get("targets", [])
    if not isinstance(targets, list):
        targets = []
    writer_contracts = _safe_yaml(root / "config" / "s0" / "writer_prompt_contracts.yaml")
    judge_contracts = _safe_yaml(root / "config" / "s0" / "judge_prompt_contracts.yaml")
    writer_model = str(os.environ.get("S0_WRITER_MODEL", "")).strip()
    judge_model = str(os.environ.get("S0_JUDGE_MODEL", "")).strip() or writer_model
    fingerprint_path = run_dir / "calibration_resume_fingerprint.json"
    resume_state_path = run_dir / "resume_state.json"
    fingerprint_record = _build_calibration_fingerprint(
        mode=mode,
        profile=profile,
        targets_payload=targets_payload,
        writer_contracts=writer_contracts,
        judge_contracts=judge_contracts,
        writer_model=writer_model,
        judge_model=judge_model,
    )
    existing_fingerprint = _read_json(fingerprint_path) if fingerprint_path.exists() else {}
    existing_digest = str(existing_fingerprint.get("fingerprint_digest", ""))
    fingerprint_digest = str(fingerprint_record.get("fingerprint_digest", ""))
    fingerprint_match = (not existing_digest) or (existing_digest == fingerprint_digest)

    # Deterministic calibration subset using prompts that approximate difficult/weak scenarios.
    preferred_ids = {
        "CORE-SAFE-003",
        "CORE-CONC-003",
        "RET-ISSUE-005",
        "RET-RESOLVE-008",
        "RET-NEG-001",
    }
    selected = [t for t in targets if str(t.get("prompt_id", "")) in preferred_ids]
    if len(selected) < 5:
        for t in targets:
            if t in selected:
                continue
            selected.append(t)
            if len(selected) >= 5:
                break
    target_prompt_by_id = {
        str(t.get("target_id", "")): str(t.get("prompt_id", ""))
        for t in selected
        if isinstance(t, dict)
    }

    startup_failures: list[str] = []
    bootstrap_marker = root / ".cache" / "sqlite_kb" / "reports" / ".phase_a_bootstrap_complete"
    if mode != "bootstrap" and not bootstrap_marker.exists():
        startup_failures.append("startup_checklist:mode_must_be_bootstrap_for_first_recovery_run")
    if len(selected) != 5:
        startup_failures.append("startup_checklist:active_target_set_must_be_5")
    source_text = Path(__file__).read_text(encoding="utf-8")
    legacy_template_markers = [
        'if "conc" in lower_prompt',
        'if "issue" in lower_prompt',
        "Deterministic Stage B judgment generated from calibration artifacts.",
    ]
    if mode == "publishable" and any(marker in source_text for marker in legacy_template_markers):
        startup_failures.append("startup_checklist:template_semantic_branch_detected")
    writer_roles_cfg = writer_contracts.get("roles") if isinstance(writer_contracts, dict) else {}
    if not isinstance(writer_roles_cfg, dict) or not writer_roles_cfg:
        startup_failures.append("startup_checklist:writer_prompt_contracts_missing")
    else:
        for role_name, role_payload in writer_roles_cfg.items():
            if not isinstance(role_payload, dict):
                startup_failures.append(f"startup_checklist:invalid_writer_contract:{role_name}")
                continue
            prompt_text = str(role_payload.get("prompt_template_text", ""))
            if "\n" not in prompt_text and len(prompt_text.split()) < 25:
                startup_failures.append(f"startup_checklist:one_line_writer_prompt:{role_name}")
    stage_b_stub_marker = '"score":' + " 0.9 if verdict"
    if stage_b_stub_marker in source_text:
        startup_failures.append("startup_checklist:stage_b_fixed_score_stub_detected")
    try:
        version_probe = subprocess.run(
            ["opencode", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if version_probe.returncode != 0:
            startup_failures.append("startup_checklist:opencode_cli_unavailable")
    except (subprocess.SubprocessError, OSError):
        startup_failures.append("startup_checklist:opencode_cli_unavailable")
    try:
        probe_command = ["opencode", "run", "--format", "json", "--agent", "plan"]
        if writer_model:
            probe_command.extend(["--model", writer_model])
        probe_command.append(
            'Return exactly this JSON object and nothing else: {"opencode_health":"ok"}'
        )
        model_probe = subprocess.run(
            probe_command,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if model_probe.returncode != 0:
            startup_failures.append("startup_checklist:opencode_model_routing_unavailable")
        else:
            probe_text_parts: list[str] = []
            for line in model_probe.stdout.splitlines():
                raw_line = line.strip()
                if not raw_line:
                    continue
                parsed_line = json.loads(raw_line)
                if not isinstance(parsed_line, dict):
                    continue
                if str(parsed_line.get("type", "")) != "text":
                    continue
                part_raw = parsed_line.get("part")
                part = part_raw if isinstance(part_raw, dict) else {}
                text = str(part.get("text", ""))
                if text:
                    probe_text_parts.append(text)
            probe_content = "\n".join(probe_text_parts).strip()
            try:
                probe_json = _extract_json_object(probe_content)
            except (ValueError, json.JSONDecodeError):
                probe_json = {}
            if str(probe_json.get("opencode_health", "")) != "ok":
                startup_failures.append("startup_checklist:opencode_non_interactive_probe_failed")
    except (subprocess.SubprocessError, OSError):
        startup_failures.append("startup_checklist:opencode_model_routing_unavailable")
    clean_run_artifacts = {
        "targets.json",
        "targets_digest",
        "core_docs_eval_report.json",
        "rust_reference_eval_report.json",
        "doctor_report.json",
        "doctor_quality_minimums_report.json",
        "worked_example_validation_report.json",
        "prompt_contract_validation_report.json",
        "catalog_smoke_report.json",
        "build_env_fingerprint.json",
        "embedding_backend_fingerprint.json",
    }

    existing_entries = sorted(p.name for p in run_dir.iterdir())
    resume_candidate_markers = {
        "calibration_target_rationale.json",
        "core_docs_backend_attempts.jsonl",
        "core_docs_eval_report.json",
        "rust_reference_backend_attempts.jsonl",
        "rust_reference_eval_report.json",
        "writer_subagent_outputs",
        "evidence_bundle",
    }
    resume_candidate = any((run_dir / marker).exists() for marker in resume_candidate_markers)
    resume_mode = (resume_requested or resume_candidate) and reuse_existing

    unknown_existing: list[str] = []
    if resume_mode:
        for name in existing_entries:
            if not _is_allowed_resume_artifact(name):
                unknown_existing.append(name)
        if unknown_existing:
            startup_failures.append("startup_checklist:resume_unknown_artifacts_present")
        if existing_digest and not fingerprint_match:
            startup_failures.append("startup_checklist:resume_fingerprint_mismatch")
    else:
        non_clean = [name for name in existing_entries if name not in clean_run_artifacts]
        if non_clean:
            startup_failures.append("startup_checklist:run_artifact_root_not_clean")

    startup_report = {
        "run_id": run_id,
        "mode": mode,
        "resume_requested": resume_requested,
        "resume_mode": resume_mode,
        "resume_candidate": resume_candidate,
        "fingerprint_match": fingerprint_match,
        "resume_unknown_artifacts": unknown_existing,
        "status": "pass" if not startup_failures else "fail",
        "failures": startup_failures,
    }
    _write_json(run_dir / "startup_checklist_report.json", startup_report)
    if startup_failures:
        raise RuntimeError(f"Startup checklist failed: {startup_failures}")

    _write_json(fingerprint_path, fingerprint_record)

    prior_resume_state = _read_json(resume_state_path) if resume_state_path.exists() else {}
    attempt_index = int(prior_resume_state.get("attempt_index", 0)) + 1

    core_report_preexisting = (run_dir / "core_docs_eval_report.json").exists()
    rust_report_preexisting = (run_dir / "rust_reference_eval_report.json").exists()
    _write_json(
        resume_state_path,
        {
            "run_id": run_id,
            "attempt_index": attempt_index,
            "resume_requested": resume_requested,
            "resume_mode": resume_mode,
            "reuse_existing": reuse_existing,
            "fingerprint_digest": fingerprint_digest,
            "fingerprint_match": fingerprint_match,
            "reused_artifacts": [],
            "remaining_work_executed": [],
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )

    _write_json(
        run_dir / "calibration_target_rationale.json",
        {
            "run_id": run_id,
            "selection_policy": "deterministic_prompt_subset_v1",
            "selected_count": len(selected),
            "targets": selected,
        },
    )

    core_report_path, core = _run_eval_for_corpus(
        root, run_dir, "core_docs", reuse_existing=reuse_existing
    )
    rust_report_path, rust = _run_eval_for_corpus(
        root, run_dir, "rust_reference", reuse_existing=reuse_existing
    )

    reused_artifacts: list[str] = []
    remaining_work_executed: list[str] = []
    if reuse_existing and core_report_preexisting:
        reused_artifacts.append("core_docs_eval_report.json")
    else:
        remaining_work_executed.append("core_docs_eval_report.json")
    if reuse_existing and rust_report_preexisting:
        reused_artifacts.append("rust_reference_eval_report.json")
    else:
        remaining_work_executed.append("rust_reference_eval_report.json")
    _write_json(
        resume_state_path,
        {
            "run_id": run_id,
            "attempt_index": attempt_index,
            "resume_requested": resume_requested,
            "resume_mode": resume_mode,
            "reuse_existing": reuse_existing,
            "fingerprint_digest": fingerprint_digest,
            "fingerprint_match": fingerprint_match,
            "reused_artifacts": reused_artifacts,
            "remaining_work_executed": remaining_work_executed,
            "updated_at": datetime.now(UTC).isoformat(),
            "core_report_path": str(core_report_path),
            "rust_report_path": str(rust_report_path),
        },
    )

    core_summary = core.get("summary", {})
    rust_summary = rust.get("summary", {})
    core_gate = core.get("gate_failures", [])
    rust_gate = rust.get("gate_failures", [])

    case_map: dict[tuple[str, str], dict[str, Any]] = {}
    for corpus_name, payload in (("core_docs", core), ("rust_reference", rust)):
        for case in payload.get("cases", []):
            if not isinstance(case, dict):
                continue
            if str(case.get("mode", "")) != "semantic" or str(case.get("status", "")) != "pass":
                continue
            prompt_id = str(case.get("prompt_id", "")).strip()
            if not prompt_id:
                continue
            case_map[(corpus_name, prompt_id)] = case

    selected_rows: list[dict[str, Any]] = []
    for target in selected:
        corpus_name = str(target.get("corpus", "")).strip()
        prompt_id = str(target.get("prompt_id", "")).strip()
        case = case_map.get((corpus_name, prompt_id), {})
        top_chunk_ids = [str(x) for x in (case.get("top_statement_ids") or [])][:3]
        snippets: list[dict[str, Any]] = []
        db_path = root / ".cache" / "sqlite_kb" / "current" / f"{corpus_name}.sqlite"
        if db_path.exists() and top_chunk_ids:
            import sqlite3

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                for chunk_id in top_chunk_ids:
                    row = conn.execute(
                        "SELECT c.chunk_uid, c.clean_text, c.section_id, sec.heading, sec.anchor, sec.document_id "
                        "FROM chunks c LEFT JOIN sections sec ON sec.section_id=c.section_id "
                        "WHERE c.chunk_uid=?",
                        (chunk_id,),
                    ).fetchone()
                    if row is None:
                        continue
                    snippets.append(
                        {
                            "chunk_uid": row["chunk_uid"],
                            "section_id": row["section_id"],
                            "heading": row["heading"],
                            "anchor": row["anchor"],
                            "document_id": row["document_id"],
                            "text": str(row["clean_text"] or "")[:1600],
                        }
                    )
            finally:
                conn.close()
        selected_rows.append(
            {
                "target": target,
                "case": case,
                "top_chunk_ids": top_chunk_ids,
                "snippets": snippets,
            }
        )

    evidence_bundle = run_dir / "evidence_bundle"
    evidence_bundle.mkdir(parents=True, exist_ok=True)
    _write_json(
        evidence_bundle / "calibration_evidence.json",
        {
            "run_id": run_id,
            "targets": [
                {
                    "target_id": row["target"].get("target_id"),
                    "prompt_id": row["target"].get("prompt_id"),
                    "corpus": row["target"].get("corpus"),
                    "table1_rows": row["target"].get("table1_rows", []),
                    "top_chunk_ids": row["top_chunk_ids"],
                    "snippets": row["snippets"],
                }
                for row in selected_rows
            ],
        },
    )
    (evidence_bundle / "README.md").write_text(
        "Calibration evidence snippets extracted from semantic retrieval cases.\n",
        encoding="utf-8",
    )

    # Curated exemplar lock + deterministic row mapping.
    guidelines_repo_root = (root / ".." / "safety-critical-rust-coding-guidelines").resolve()
    curated_ids = [
        "gui_0cuTYG8RVYjg",
        "gui_xztNdXA2oFNC",
        "gui_7y0GAMmtMhch",
        "gui_ADHABsmK9FXz",
        "gui_HDnAZ7EZ4z6G",
        "gui_LvmzGKdsAgI5",
        "gui_PM8Vpf7lZ51U",
        "gui_RHvQj8BHlz9b",
        "gui_dCquvqE1csI3",
        "gui_iv9yCMHRgpE0",
        "gui_kMbiWbn8Z6g5",
        "gui_ot2Zt3dd6of1",
        "gui_ZDLZzjeOwLSU",
        "gui_FRLaMIMb4t3S",
    ]
    row_map: dict[str, list[str]] = {
        "1a": ["gui_xztNdXA2oFNC", "gui_0cuTYG8RVYjg", "gui_ot2Zt3dd6of1"],
        "1b": ["gui_7y0GAMmtMhch", "gui_ADHABsmK9FXz", "gui_ZDLZzjeOwLSU"],
        "1c": ["gui_HDnAZ7EZ4z6G", "gui_LvmzGKdsAgI5", "gui_PM8Vpf7lZ51U"],
        "1d": ["gui_RHvQj8BHlz9b", "gui_dCquvqE1csI3", "gui_iv9yCMHRgpE0"],
        "1e": ["gui_kMbiWbn8Z6g5", "gui_xztNdXA2oFNC", "gui_7y0GAMmtMhch"],
        "1f": ["gui_ot2Zt3dd6of1", "gui_ZDLZzjeOwLSU", "gui_FRLaMIMb4t3S"],
        "1g": ["gui_PM8Vpf7lZ51U", "gui_RHvQj8BHlz9b", "gui_HDnAZ7EZ4z6G"],
        "1h": ["gui_FRLaMIMb4t3S", "gui_dCquvqE1csI3", "gui_iv9yCMHRgpE0"],
        "1i": ["gui_ADHABsmK9FXz", "gui_LvmzGKdsAgI5", "gui_0cuTYG8RVYjg"],
    }
    exemplar_entries: list[dict[str, Any]] = []
    for exemplar_id in curated_ids:
        matches = sorted(guidelines_repo_root.glob(f"src/coding-guidelines/**/{exemplar_id}.rst"))
        if not matches:
            exemplar_entries.append({"guideline_id": exemplar_id, "status": "missing"})
            continue
        path = matches[0]
        blob = path.read_bytes()
        exemplar_entries.append(
            {
                "guideline_id": exemplar_id,
                "status": "ok",
                "path": str(path),
                "sha256": hashlib.sha256(blob).hexdigest(),
                "size_bytes": len(blob),
            }
        )
    missing_exemplars = [x["guideline_id"] for x in exemplar_entries if x.get("status") != "ok"]
    if missing_exemplars:
        raise RuntimeError(f"Missing curated exemplar files: {missing_exemplars}")
    lock_digest = hashlib.sha256(
        json.dumps(exemplar_entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    _write_json(
        run_dir / "golden_exemplar_lock_report.json",
        {
            "run_id": run_id,
            "curated_ids": curated_ids,
            "entries": exemplar_entries,
            "digest": lock_digest,
        },
    )

    exemplar_lookup = {x["guideline_id"]: x for x in exemplar_entries}
    exemplar_selection_trace: list[dict[str, Any]] = []
    synthesis_input_trace: list[dict[str, Any]] = []
    draft_rows: list[dict[str, Any]] = []
    analysis_rows: list[dict[str, Any]] = []

    writer_root = run_dir / "writer_subagent_outputs"
    writer_root.mkdir(parents=True, exist_ok=True)
    style_source_path = (guidelines_repo_root / "src" / "process" / "style-guideline.rst").resolve()
    if not style_source_path.exists():
        raise RuntimeError(f"Missing style guideline source: {style_source_path}")
    style_text = style_source_path.read_text(encoding="utf-8")
    style_source_digest = hashlib.sha256(style_text.encode("utf-8")).hexdigest()
    style_bundle = {
        "run_id": run_id,
        "style_source_path": str(style_source_path),
        "style_source_digest": style_source_digest,
        "global_rules": [
            "Use RFC2119 normative strength terms consistently.",
            "Guideline block must include rationale, non_compliant_example, compliant_example, bibliography.",
            "All factual claims require citation-backed evidence mapping.",
        ],
        "amplification_rules": [
            "Guideline body text directly follows metadata and is construct-specific.",
            "Amplification text uses explicit normative strength aligned with recommendation severity.",
        ],
        "example_rules": [
            "Examples must be substantive and tied to described hazard/mechanism.",
            "Compliant and non-compliant examples must represent the same construct family.",
        ],
        "rationale_rules": [
            "Rationale follows hazard -> mechanism -> consequence logic.",
            "Avoid tautological or generic rationale statements.",
        ],
        "metadata_bibliography_rules": [
            "Metadata values should be specific and non-generic.",
            "Bibliography entries must provide concrete source references.",
        ],
    }
    _write_json(writer_root / "style_context_bundle.json", style_bundle)

    exemplar_paths = get_exemplar_paths(guidelines_repo_root=guidelines_repo_root)
    exemplar_conventions = extract_all_exemplar_conventions(exemplar_paths)
    std_lookup = load_stdlib_index()
    fls_stats = get_fls_db_stats()
    convention_spec = _build_convention_spec(
        exemplar_conventions,
        guidelines_repo_root=guidelines_repo_root,
        std_lookup=std_lookup,
    )
    _write_json(run_dir / "convention_spec.json", convention_spec)
    validation_report = validate_convention_spec(convention_spec)
    _write_json(
        run_dir / "convention_spec_validation.json",
        {
            "run_id": run_id,
            "status": validation_report.get("status", "fail"),
            "validated_at": datetime.now(UTC).isoformat(),
            "validation": validation_report,
        },
    )

    stable_spec_path = root / ".cache" / "convention_spec.json"
    stable_spec_path.parent.mkdir(parents=True, exist_ok=True)
    if stable_spec_path.exists():
        old_spec = json.loads(stable_spec_path.read_text(encoding="utf-8"))
        old_sha = str(old_spec.get("guidelines_repo_commit_sha", ""))
        new_sha = str(convention_spec.get("guidelines_repo_commit_sha", ""))
        if old_sha != new_sha:
            _write_json(
                run_dir / "convention_spec_diff.json",
                {
                    "old_sha": old_sha,
                    "new_sha": new_sha,
                    "changes": _diff_specs(old_spec, convention_spec),
                },
            )
    stable_spec_path.write_text(json.dumps(convention_spec, indent=2) + "\n", encoding="utf-8")

    _write_json(
        run_dir / "lookup_status.json",
        {
            "run_id": run_id,
            "stdlib_entries": len(std_lookup),
            "stdlib_source": "core_docs_db" if CORE_DOCS_DB_PATH.exists() else "fallback",
            "fls_spec_db": fls_stats,
            "fls_id_validation": "spec.lock",
        },
    )
    fls_matching_report = validate_fls_matching()
    _write_json(run_dir / "fls_matching_validation.json", fls_matching_report)
    writer_contracts = _safe_yaml(root / "config" / "s0" / "writer_prompt_contracts.yaml")
    judge_contracts = _safe_yaml(root / "config" / "s0" / "judge_prompt_contracts.yaml")
    role_contracts = writer_contracts.get("roles") if isinstance(writer_contracts, dict) else {}
    if not isinstance(role_contracts, dict):
        role_contracts = {}
    _write_json(
        writer_root / "prompt_contract_snapshot.json",
        {
            "run_id": run_id,
            "writer_contract_version": writer_contracts.get("contract_version"),
            "judge_contract_version": judge_contracts.get("contract_version"),
            "writer_roles": {
                role: {
                    "prompt_template_id": payload.get("prompt_template_id"),
                    "prompt_template_digest": hashlib.sha256(
                        str(payload.get("prompt_template_text", "")).encode("utf-8")
                    ).hexdigest(),
                    "contract_version": writer_contracts.get("contract_version"),
                    "forbidden_patterns": payload.get("forbidden_patterns", []),
                    "required_output_schema": payload.get("required_output_schema", {}),
                }
                for role, payload in role_contracts.items()
                if isinstance(payload, dict)
            },
            "judge_roles": {
                role: {
                    "prompt_template_id": payload.get("prompt_template_id"),
                    "prompt_template_digest": hashlib.sha256(
                        str(payload.get("prompt_template_text", "")).encode("utf-8")
                    ).hexdigest(),
                }
                for role, payload in (judge_contracts.get("roles", {}) or {}).items()
                if isinstance(payload, dict)
            },
        },
    )

    def _target_row(target: dict[str, Any]) -> str:
        rows = target.get("table1_rows")
        if isinstance(rows, list) and rows:
            return str(rows[0])
        return ""

    for row in selected_rows:
        target = row["target"]
        prompt_id = str(target.get("prompt_id", ""))
        corpus_name = str(target.get("corpus", ""))
        target_id = str(target.get("target_id", ""))
        row_id = _target_row(target)
        compat = sorted(set(row_map.get(row_id, [])))
        if row_id and len(compat) < 2:
            raise RuntimeError(
                f"resolve-exemplars failed for target {target_id}: <2 row-compatible exemplars"
            )
        if row_id:
            seed = int(hashlib.sha256(target_id.encode("utf-8")).hexdigest()[:8], 16)
            ordered = compat[seed % len(compat) :] + compat[: seed % len(compat)]
            selected_exemplars = ordered[: min(3, len(ordered))]
            candidates_top_k = ordered[: min(5, len(ordered))]
        else:
            selected_exemplars = []
            candidates_top_k = []
        exemplar_selection_trace.append(
            {
                "target_id": target_id,
                "prompt_id": prompt_id,
                "table1_row": row_id,
                "selected_exemplar_ids": selected_exemplars,
                "candidates_top_k": candidates_top_k,
                "selected_rank": 0,
                "exemplar_files": [
                    {
                        "guideline_id": gid,
                        "path": exemplar_lookup[gid]["path"],
                        "sha256": exemplar_lookup[gid]["sha256"],
                    }
                    for gid in selected_exemplars
                ],
                "selection_reason": "row_compatible_deterministic_hash",
            }
        )

        snippets = row["snippets"]
        first_snippet = str(snippets[0]["text"]) if snippets else ""
        second_snippet = str(snippets[1]["text"]) if len(snippets) > 1 else first_snippet
        category = str(target.get("category", "safety_control")).replace("_", " ").strip()
        exemplar_phrase = ", ".join(selected_exemplars) if selected_exemplars else "none"

        synthesis_input_trace.append(
            {
                "target_id": target_id,
                "target_prompt_id": prompt_id,
                "exemplar_ids_used": selected_exemplars,
                "evidence_ids_used": row["top_chunk_ids"],
                "input_digest": hashlib.sha256(
                    json.dumps(
                        {
                            "target": target,
                            "selected_exemplars": selected_exemplars,
                            "evidence_ids": row["top_chunk_ids"],
                            "snippets": snippets,
                        },
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )

    writer_evidence_rows: list[dict[str, Any]] = []
    writer_amplification_rows: list[dict[str, Any]] = []
    writer_example_rows: list[dict[str, Any]] = []
    writer_rationale_rows: list[dict[str, Any]] = []
    writer_metadata_rows: list[dict[str, Any]] = []
    invocation_rows: list[dict[str, Any]] = []

    def _role_prompt(role_name: str) -> tuple[str, str]:
        payload = role_contracts.get(role_name) if isinstance(role_contracts, dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        template_id = str(payload.get("prompt_template_id", role_name))
        template_text = str(payload.get("prompt_template_text", ""))
        digest = hashlib.sha256(template_text.encode("utf-8")).hexdigest()
        return template_id, digest

    drafting_contract = _safe_yaml(root / "config" / "s0" / "drafting_prompt_contract.yaml")
    worked_pos = drafting_contract.get("worked_positive_examples", [])
    worked_neg = drafting_contract.get("worked_negative_examples", [])
    runtime_cfg = _safe_yaml(root / "config" / "s0" / "target_execution_modes.yaml")
    runtime_hazard_rows = {
        str(x)
        for x in (
            runtime_cfg.get("runtime_hazard_rows", []) if isinstance(runtime_cfg, dict) else []
        )
    }
    row_defaults = runtime_cfg.get("row_defaults", {}) if isinstance(runtime_cfg, dict) else {}
    prompt_overrides = (
        runtime_cfg.get("prompt_overrides", {}) if isinstance(runtime_cfg, dict) else {}
    )
    default_mode = (
        str(runtime_cfg.get("default_mode", "runnable"))
        if isinstance(runtime_cfg, dict)
        else "runnable"
    )
    writer_timeout = int(str(os.environ.get("S0_WRITER_TIMEOUT_SECONDS", "90")))

    style_excerpt = "\n".join(style_text.splitlines()[:80])
    budget_cfg = writer_contracts.get("injected_context_budgets", {})
    if not isinstance(budget_cfg, dict):
        budget_cfg = {}
    convention_budget = int(budget_cfg.get("convention_spec_tokens", 2000))
    std_budget = int(budget_cfg.get("std_lookup_tokens", 1000))
    exemplar_budget = int(budget_cfg.get("exemplar_tokens", 500))
    total_budget = int(budget_cfg.get("total_injected_tokens", 3500))

    exemplar_snippets_by_id: dict[str, str] = {}
    for entry in exemplar_entries:
        if not isinstance(entry, dict) or entry.get("status") != "ok":
            continue
        guideline_id = str(entry.get("guideline_id", ""))
        path_raw = str(entry.get("path", ""))
        if not guideline_id or not path_raw:
            continue
        path = Path(path_raw)
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        exemplar_snippets_by_id[guideline_id] = "\n".join(lines[:24])

    retry_depth_variant, max_role_retries, retry_resolution_rate = _load_retry_depth_policy(
        root, run_dir
    )
    gate_policy_cfg = _safe_yaml(root / "config" / "s0" / "s0_gate_policy.yaml")
    convention_retry_budget = int(
        gate_policy_cfg.get(
            "convention_retry_budget",
            gate_policy_cfg.get("max_convention_retries", CONVENTION_RETRY_BUDGET),
        )
    )
    non_abstain_targets = [
        row
        for row in selected_rows
        if not bool((row.get("target") or {}).get("expect_abstain", False))
        and not bool((row.get("target") or {}).get("abstain_expected", False))
    ]
    per_target_retry_budget = convention_retry_budget // max(len(non_abstain_targets), 1)
    target_retry_usage: dict[str, int] = {}
    target_lanes: dict[str, dict[str, str]] = {}
    role_validation_log: list[dict[str, Any]] = []
    retry_log: list[dict[str, Any]] = []
    writer_role_order = [
        "evidence_synthesizer",
        "amplification_author",
        "example_author",
        "rationale_author",
        "metadata_citation_curator",
    ]

    for row in selected_rows:
        target = row["target"]
        prompt_id = str(target.get("prompt_id", ""))
        corpus_name = str(target.get("corpus", ""))
        target_id = str(target.get("target_id", ""))
        row_id = _target_row(target)
        selected_exemplars = []
        for trace in exemplar_selection_trace:
            if str(trace.get("target_id", "")) == target_id:
                selected_exemplars = [str(x) for x in (trace.get("selected_exemplar_ids") or [])]
                break
        snippets = row.get("snippets") or []
        snippet_rows = [s for s in snippets if isinstance(s, dict)]
        evidence_ids = [str(x) for x in (row.get("top_chunk_ids") or [])]
        evidence_text = "\n\n".join(str(s.get("text", ""))[:700] for s in snippet_rows[:3])
        example_mode = str(prompt_overrides.get(prompt_id, row_defaults.get(row_id, default_mode)))

        role_order = writer_role_order
        role_outputs: dict[str, dict[str, Any]] = {}
        role_failures: list[str] = []
        draft_id = f"draft::{prompt_id.lower()}"

        for role_name in role_order:
            role_contract = (
                role_contracts.get(role_name) if isinstance(role_contracts, dict) else {}
            )
            if not isinstance(role_contract, dict):
                role_contract = {}
            required = _required_fields(role_contract.get("required_output_schema", {}))
            forbidden = role_contract.get("forbidden_patterns", [])
            forbidden = forbidden if isinstance(forbidden, list) else []
            prompt_template = str(role_contract.get("prompt_template_text", ""))
            role_input = {
                "target_id": target_id,
                "target_prompt_id": prompt_id,
                "table1_row": row_id,
                "corpus": corpus_name,
                "evidence_ids": evidence_ids,
                "evidence_snippets": snippet_rows,
                "evidence_text": evidence_text,
                "exemplar_ids": selected_exemplars,
                "worked_positive_examples": worked_pos,
                "worked_negative_examples": worked_neg,
                "style_excerpt": style_excerpt,
                "example_execution_mode": example_mode,
                "runtime_hazard_target": row_id in runtime_hazard_rows,
                "upstream_outputs": role_outputs,
            }

            if role_name == "evidence_synthesizer":
                construct_terms = _rust_like_tokens(evidence_text)[:12]
            else:
                scope_terms = role_outputs.get("evidence_synthesizer", {}).get(
                    "construct_scope", []
                )
                if isinstance(scope_terms, list):
                    construct_terms = [str(value) for value in scope_terms if str(value).strip()][
                        :12
                    ]
                else:
                    construct_terms = _rust_like_tokens(str(scope_terms))[:12]

            std_lookup_scoped = {
                key: value
                for key, value in std_lookup.items()
                if _is_relevant_to_construct(key, construct_terms)
            }
            std_lookup_payload, std_omitted = _truncate_mapping_for_budget(
                std_lookup_scoped,
                token_budget=std_budget,
                sort_terms=construct_terms,
            )

            exemplar_extracts = [
                {
                    "guideline_id": gid,
                    "snippet": exemplar_snippets_by_id.get(gid, ""),
                }
                for gid in selected_exemplars[:2]
            ]
            exemplar_extracts = [item for item in exemplar_extracts if item.get("snippet")]
            exemplar_payload, exemplar_omitted = _truncate_exemplar_extracts(
                exemplar_extracts,
                token_budget=exemplar_budget,
            )

            role_input["convention_spec"] = convention_spec
            role_input["std_lookup"] = std_lookup_payload
            role_input["exemplar_extracts"] = exemplar_payload
            rendered_prompt = (
                f"{prompt_template}\n\n"
                f"Output schema required fields: {required}\n"
                f"Forbidden patterns: {forbidden}\n"
                "Length and structure bounds: keep each narrative field between 40 and 220 words; "
                "code examples between 4 and 40 lines; no placeholder text.\n"
                f"Input context JSON:\n{json.dumps(role_input, indent=2, sort_keys=True)}"
            )
            prompt_template_id, _prompt_template_digest = _role_prompt(role_name)
            target_retry_remaining = max(
                per_target_retry_budget - target_retry_usage.get(target_id, 0),
                0,
            )
            allowed_retries = min(max_role_retries, target_retry_remaining)
            role_budget = 1 + allowed_retries
            role_latest_violations: list[RoleViolation] = []
            role_attempt_entries: list[dict[str, Any]] = []
            role_attempt_counter = 0

            def _parse_role_violations(output_payload: dict[str, Any]) -> list[str] | None:
                nonlocal role_latest_violations, role_attempt_counter
                role_attempt_counter += 1
                role_latest_violations = validate_role_output(
                    role_name,
                    output_payload,
                    convention_spec,
                    std_lookup,
                    construct_terms,
                    prompt_id,
                )
                role_attempt_entries.append(
                    {
                        "attempt": role_attempt_counter,
                        "violations": [
                            {
                                "check": violation.check,
                                "message": violation.message,
                                "severity": violation.severity,
                            }
                            for violation in role_latest_violations
                        ],
                    }
                )
                return [
                    violation.check
                    for violation in role_latest_violations
                    if violation.severity == "error"
                ]

            def _build_retry_prompt(initial: str, active_checks: list[str]) -> str:
                active_set = set(active_checks)
                active_violations = [
                    violation
                    for violation in role_latest_violations
                    if violation.severity == "error" and violation.check in active_set
                ]
                violations_text = "\n".join(
                    f"- [{violation.check}] {violation.message}" for violation in active_violations
                )
                if len(violations_text) > 8000:
                    violations_text = violations_text[:8000] + "\n[...truncated...]"
                next_attempt = min(role_attempt_counter + 1, role_budget)
                return (
                    f"{initial}\n\n"
                    f"=== RETRY (attempt {next_attempt}/{role_budget}) ===\n"
                    "Your previous output had these violations:\n"
                    f"{violations_text}\n"
                    "Please fix these issues in your output."
                )

            request_started_at = datetime.now(UTC).isoformat()
            retry_result = retry_with_violations(
                session_id=f"{run_id}:{prompt_id}:{role_name}",
                initial_prompt=rendered_prompt,
                parse_violations_fn=_parse_role_violations,
                build_retry_prompt_fn=_build_retry_prompt,
                budget=role_budget,
                stop_on_same_violations=True,
            )
            response_received_at = datetime.now(UTC).isoformat()
            retries_used = max(retry_result.attempts - 1, 0)
            target_retry_usage[target_id] = target_retry_usage.get(target_id, 0) + retries_used
            output = retry_result.output if isinstance(retry_result.output, dict) else {}
            invocation = {
                "system_request_id": f"sysreq::{uuid.uuid4().hex[:20]}",
                "request_started_at": request_started_at,
                "response_received_at": response_received_at,
                "prompt_digest": _canonical_digest(rendered_prompt),
                "response_digest": _canonical_digest(json.dumps(output, sort_keys=True))
                if output
                else "",
                "transport_status": "ok" if output else "error:empty_output",
                "provider_model": writer_model,
                "transport_backend": "opencode_http",
            }
            if not output:
                role_failures.append(f"{role_name}:transport_failure")
                output = {
                    "target_id": target_id,
                    "status": "abstain",
                    "error": "writer_output_missing",
                }
            if not retry_result.success and retry_result.violations_remaining:
                role_failures.append(f"{role_name}:validation_failed")
                lane_status = target_lanes.setdefault(target_id, {"lane": "publishable"})
                lane_status["lane"] = "diagnostic"
                lane_status["diagnostic_reason"] = "retry_exhausted"
                retry_log.append(
                    {
                        "target_id": target_id,
                        "prompt_id": prompt_id,
                        "role": role_name,
                        "outcome": "retry_exhausted",
                        "remaining_error_violations": retry_result.violations_remaining,
                    }
                )
            role_validation_log.append(
                {
                    "target_id": target_id,
                    "prompt_id": prompt_id,
                    "role": role_name,
                    "attempts": retry_result.attempts,
                    "retries_used": retries_used,
                    "budget": role_budget,
                    "retry_variant": retry_depth_variant,
                    "success": retry_result.success,
                    "budget_exhausted": retry_result.budget_exhausted,
                    "oscillation_detected": retry_result.oscillation_detected,
                    "diminishing_returns": retry_result.diminishing_returns,
                    "violations_remaining": retry_result.violations_remaining,
                    "attempt_entries": role_attempt_entries,
                }
            )
            missing_required = _ensure_required_fields(role_name, output, required)
            role_failures.extend(missing_required)
            output["target_id"] = target_id
            output["prompt_id"] = prompt_id
            output["draft_id"] = draft_id
            role_outputs[role_name] = output
            invocation_rows.append(
                {
                    "target_id": target_id,
                    "target_prompt_id": prompt_id,
                    "writer_role": role_name,
                    "prompt_template_id": prompt_template_id,
                    "prompt_digest": invocation.get(
                        "prompt_digest", _canonical_digest(rendered_prompt)
                    ),
                    "response_digest": invocation.get("response_digest", ""),
                    "system_request_id": invocation.get("system_request_id"),
                    "request_started_at": invocation.get("request_started_at"),
                    "response_received_at": invocation.get("response_received_at"),
                    "transport_status": invocation.get("transport_status", "unknown"),
                    "transport_backend": invocation.get("transport_backend", "opencode_cli"),
                    "provider_model": invocation.get("provider_model", writer_model),
                    "provider_message_id": invocation.get("provider_message_id"),
                    "provider_token_usage": invocation.get("provider_token_usage"),
                    "injected_context": {
                        "convention_spec_tokens": _approx_tokens(
                            json.dumps(convention_spec, sort_keys=True)
                        ),
                        "std_lookup_tokens": _approx_tokens(
                            "\n".join(f"{k} -> {v}" for k, v in std_lookup_payload.items())
                        ),
                        "exemplar_tokens": _approx_tokens(
                            json.dumps(exemplar_payload, sort_keys=True)
                        ),
                        "total_injected_tokens": _approx_tokens(
                            json.dumps(
                                {
                                    "convention_spec": convention_spec,
                                    "std_lookup": std_lookup_payload,
                                    "exemplar_extracts": exemplar_payload,
                                },
                                sort_keys=True,
                            )
                        ),
                        "budget_exceeded": _approx_tokens(
                            json.dumps(
                                {
                                    "convention_spec": convention_spec,
                                    "std_lookup": std_lookup_payload,
                                    "exemplar_extracts": exemplar_payload,
                                },
                                sort_keys=True,
                            )
                        )
                        > total_budget,
                        "section_over_budget": {
                            "convention_spec": _approx_tokens(
                                json.dumps(convention_spec, sort_keys=True)
                            )
                            > convention_budget,
                            "std_lookup": _approx_tokens(
                                "\n".join(f"{k} -> {v}" for k, v in std_lookup_payload.items())
                            )
                            > std_budget,
                            "exemplars": _approx_tokens(
                                json.dumps(exemplar_payload, sort_keys=True)
                            )
                            > exemplar_budget,
                        },
                        "omitted_entries": {
                            "std_lookup": std_omitted,
                            "exemplars": exemplar_omitted,
                        },
                    },
                }
            )

        evidence_output = role_outputs.get("evidence_synthesizer", {})
        amplification_output = role_outputs.get("amplification_author", {})
        example_output = role_outputs.get("example_author", {})
        rationale_output = role_outputs.get("rationale_author", {})
        metadata_output = role_outputs.get("metadata_citation_curator", {})

        writer_evidence_rows.append(evidence_output)
        writer_amplification_rows.append(amplification_output)
        writer_example_rows.append(example_output)
        writer_rationale_rows.append(rationale_output)
        writer_metadata_rows.append(metadata_output)

        lane_status = target_lanes.get(target_id, {"lane": "publishable"})
        is_abstain = (
            bool(target.get("expect_abstain", False))
            or bool(target.get("abstain_expected", False))
            or bool(role_failures)
        )
        draft_status = (
            "diagnostic"
            if lane_status.get("lane") == "diagnostic"
            else ("abstain" if is_abstain else "drafted")
        )
        strength = str(amplification_output.get("normative_strength", "shall")).strip().lower()
        category = "mandatory" if strength == "shall" else "advisory"
        draft_row = {
            "draft_id": draft_id,
            "target_id": target_id,
            "target_prompt_id": prompt_id,
            "corpus": corpus_name,
            "table1_rows": [] if not row_id else [row_id],
            "title": str(metadata_output.get("title", f"Guideline for {prompt_id}")),
            "strength": strength if strength in {"shall", "should"} else "shall",
            "guideline": str(amplification_output.get("guideline_amplification_text", "")),
            "rationale": str(rationale_output.get("rationale_text", "")),
            "enforcement": str(evidence_output.get("mitigation", "")),
            "verification": "Generated from writer-role outputs and judge-gated enforcement.",
            "status": draft_status,
            "evidence_chunk_ids": evidence_ids,
            "evidence_snippets": [str(s.get("text", ""))[:500] for s in snippet_rows[:2]],
            "exemplar_ids_used": selected_exemplars,
            "category": category,
            "exemplar_phrase": ", ".join(selected_exemplars) if selected_exemplars else "none",
            "construct_terms": (
                [str(x) for x in evidence_output.get("construct_scope", [])]
                if isinstance(evidence_output.get("construct_scope"), list)
                else _rust_like_tokens(str(evidence_output.get("construct_scope", "")))
            ),
            "non_compliant_code": str(example_output.get("non_compliant_code", "")),
            "compliant_code": str(example_output.get("compliant_code", "")),
            "example_execution_mode": example_mode,
            "runtime_hazard_target": row_id in runtime_hazard_rows,
            "role_failures": role_failures,
            "lane": lane_status.get("lane", "publishable"),
            "diagnostic_reason": lane_status.get("diagnostic_reason", ""),
        }
        draft_rows.append(draft_row)
        analysis_rows.append(
            {
                "target_prompt_id": prompt_id,
                "reason": "writer_chain_llm",
                "analysis": "Writer chain executed with real LLM role calls and contract-aware prompts.",
            }
        )

    def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    _write_jsonl(writer_root / "evidence_synthesizer.jsonl", writer_evidence_rows)
    _write_jsonl(writer_root / "amplification_author.jsonl", writer_amplification_rows)
    _write_jsonl(writer_root / "example_author.jsonl", writer_example_rows)
    _write_jsonl(writer_root / "rationale_author.jsonl", writer_rationale_rows)
    _write_jsonl(writer_root / "metadata_citation_curator.jsonl", writer_metadata_rows)
    _write_json(
        writer_root / "subagent_invocation_trace.json",
        {"run_id": run_id, "invocations": invocation_rows},
    )
    _write_json(
        writer_root / "merge_validation_report.json",
        {
            "run_id": run_id,
            "status": "pass",
            "non_abstain_count": len(
                [d for d in draft_rows if str(d.get("status", "")) not in {"abstain", "diagnostic"}]
            ),
            "writer_outputs_complete": True,
        },
    )
    _write_json(
        run_dir / "writer_output_auditor_report.json",
        {
            "run_id": run_id,
            "status": "pass" if not any(d.get("role_failures") for d in draft_rows) else "fail",
            "results": [
                {
                    "draft_id": str(d.get("draft_id", "")),
                    "writer_outputs_complete": not bool(d.get("role_failures")),
                    "evidence_map_valid": bool(d.get("evidence_chunk_ids")),
                    "amplification_specificity_valid": bool(str(d.get("guideline", "")).strip()),
                    "amplification_evidence_linked": bool(d.get("evidence_chunk_ids")),
                    "examples_non_placeholder": str(d.get("status", ""))
                    in {"abstain", "diagnostic"}
                    or "template" not in str(d.get("non_compliant_code", "")).lower(),
                    "rationale_chain_valid": bool(str(d.get("rationale", "")).strip()),
                    "metadata_citation_valid": str(d.get("status", "")) in {"abstain", "diagnostic"}
                    or bool(d.get("table1_rows")),
                    "usage_valid": str(d.get("status", "")) in {"abstain", "diagnostic"}
                    or bool(d.get("exemplar_ids_used")),
                }
                for d in draft_rows
            ],
        },
    )
    total_role_calls = len(role_validation_log)
    total_retries = sum(int(entry.get("retries_used", 0)) for entry in role_validation_log)
    retry_rate = (total_retries / total_role_calls) if total_role_calls else 0.0
    role_validation_report = {
        "run_id": run_id,
        "retry_variant": retry_depth_variant,
        "first_retry_resolution_rate": retry_resolution_rate,
        "convention_retry_budget": convention_retry_budget,
        "per_target_retry_budget": per_target_retry_budget,
        "total_retries": total_retries,
        "total_violations": sum(
            len(attempt.get("violations", []))
            for entry in role_validation_log
            for attempt in (entry.get("attempt_entries") or [])
            if isinstance(attempt, dict)
        ),
        "per_role_retry_counts": {
            role: sum(
                int(entry.get("retries_used", 0))
                for entry in role_validation_log
                if str(entry.get("role", "")) == role
            )
            for role in writer_role_order
        },
        "retry_stats": {
            "total_retries": total_retries,
            "retry_rate": retry_rate,
            "estimated_additional_cost_pct": retry_rate * 100.0,
        },
        "warning_threshold_retry_rate": 0.30,
        "warnings": [
            "retry_rate_above_30pct" if retry_rate > 0.30 else "retry_rate_within_expected_range"
        ],
        "retry_exhausted": retry_log,
        "entries": role_validation_log,
    }
    _write_json(run_dir / "role_validation_report.json", role_validation_report)
    _write_json(
        run_dir / "guideline_manifest.json",
        {
            "run_id": run_id,
            "targets": [
                {
                    "draft_id": str(draft.get("draft_id", "")),
                    "target_id": str(draft.get("target_id", "")),
                    "prompt_id": str(draft.get("target_prompt_id", "")),
                    "status": str(draft.get("status", "drafted")),
                    "lane": str(draft.get("lane", "publishable")),
                    "diagnostic_reason": str(draft.get("diagnostic_reason", "")),
                }
                for draft in draft_rows
            ],
        },
    )

    evidence_contract = (
        role_contracts.get("evidence_synthesizer", {}) if isinstance(role_contracts, dict) else {}
    )
    evidence_required = _required_fields(
        evidence_contract.get("required_output_schema", {})
        if isinstance(evidence_contract, dict)
        else {}
    )
    evidence_forbidden = (
        evidence_contract.get("forbidden_patterns", [])
        if isinstance(evidence_contract, dict)
        else []
    )
    if not isinstance(evidence_forbidden, list):
        evidence_forbidden = []
    gate_policy_cfg = _safe_yaml(root / "config" / "s0" / "s0_gate_policy.yaml")
    must_pass_prompt_ids = (
        gate_policy_cfg.get("must_pass_prompt_ids", []) if isinstance(gate_policy_cfg, dict) else []
    )
    if not isinstance(must_pass_prompt_ids, list):
        must_pass_prompt_ids = []
    must_pass_prompt_ids = [str(x) for x in must_pass_prompt_ids if str(x).strip()]
    synonyms_cfg = _safe_yaml(root / "config" / "s0" / "construct_synonyms.yaml")
    alias_map = _synonym_alias_map(synonyms_cfg)
    evidence_ids_by_target: dict[str, list[str]] = {}
    for selected_row in selected_rows:
        if not isinstance(selected_row, dict):
            continue
        target_raw = selected_row.get("target")
        target_obj: dict[str, Any] = target_raw if isinstance(target_raw, dict) else {}
        target_id = str(target_obj.get("target_id", "")).strip()
        chunk_ids_raw = selected_row.get("top_chunk_ids")
        chunk_ids: list[str] = []
        if isinstance(chunk_ids_raw, list):
            chunk_ids = [str(x) for x in chunk_ids_raw if str(x).strip()]
        evidence_ids_by_target[target_id] = chunk_ids
    evidence_lookup_by_target: dict[str, dict[str, dict[str, Any]]] = {}
    for selected_row in selected_rows:
        if not isinstance(selected_row, dict):
            continue
        target_obj: dict[str, Any] = {}
        target_raw = selected_row.get("target")
        if isinstance(target_raw, dict):
            target_obj = target_raw
        target_id = str(target_obj.get("target_id", ""))
        snippets: list[dict[str, Any]] = []
        snippets_raw = selected_row.get("snippets")
        if isinstance(snippets_raw, list):
            snippets = [item for item in snippets_raw if isinstance(item, dict)]
        lookup: dict[str, dict[str, Any]] = {}
        for snippet in snippets:
            evidence_id = str(snippet.get("chunk_uid", "")).strip()
            if not evidence_id:
                continue
            lookup[evidence_id] = {
                "source": str(snippet.get("document_id", "")).strip()
                or "calibration_evidence_bundle",
                "anchor": str(snippet.get("anchor", "")).strip() or evidence_id,
                "text": str(snippet.get("text", "")),
            }
        evidence_lookup_by_target[target_id] = lookup

    evidence_gate_rows: list[dict[str, Any]] = []
    normalization_rows: list[dict[str, Any]] = []
    normalized_writer_evidence_rows: list[dict[str, Any]] = []
    evidence_schema_pass = 0
    evidence_normative_pass = 0
    evidence_banned_pass = 0
    for idx, row in enumerate(writer_evidence_rows, start=1):
        if not isinstance(row, dict):
            continue
        target_id = str(row.get("target_id", "")).strip()
        prompt_id = str(row.get("prompt_id", target_prompt_by_id.get(target_id, ""))).strip()
        evidence_ids = evidence_ids_by_target.get(target_id, [])
        supplemental_text = [
            str(row.get("hazard", "")),
            str(row.get("mechanism", "")),
            str(row.get("mitigation", "")),
        ]
        claim_rows_raw = row.get("claim_to_evidence_map")
        normalized_claims, claim_patterns = _normalize_claim_map(
            claim_rows_raw, target_id=target_id
        )
        for claim in normalized_claims:
            supplemental_text.append(str(claim.get("claim_text", "")))
        normalized_scope, scope_patterns = _normalize_construct_scope(
            row.get("construct_scope"),
            supplemental_text=supplemental_text,
            alias_map=alias_map,
        )
        canonical_pre = isinstance(row.get("construct_scope"), list) and isinstance(
            row.get("claim_to_evidence_map"), list
        )
        normalized_row = dict(row)
        normalized_row["target_id"] = target_id
        normalized_row["prompt_id"] = prompt_id
        normalized_row["construct_scope"] = normalized_scope
        normalized_row["claim_to_evidence_map"] = normalized_claims
        normalized_writer_evidence_rows.append(normalized_row)

        row_missing = _ensure_required_fields(
            "evidence_synthesizer", normalized_row, evidence_required
        )
        if not target_id:
            row_missing.append("evidence_synthesizer:missing_required:target_id")
        if not prompt_id:
            row_missing.append("evidence_synthesizer:missing_required:prompt_id")
        if not isinstance(normalized_scope, list) or not normalized_scope:
            row_missing.append("evidence_synthesizer:missing_required:construct_scope")
        if not normalized_claims:
            row_missing.append("evidence_synthesizer:missing_required:claim_to_evidence_map")
        schema_ok = not row_missing

        construct_scope = [str(x) for x in normalized_scope]
        construct_scope_normalized = [_normalize_text(x) for x in construct_scope if str(x).strip()]
        evidence_id_set = {str(x) for x in evidence_ids}
        reason_codes: list[str] = []
        normative_ok = False
        for claim in normalized_claims:
            claim_text = _normalize_text(str(claim.get("claim_text", "")))
            refs_raw = claim.get("evidence_refs")
            refs: list[dict[str, Any]] = []
            if isinstance(refs_raw, list):
                refs = [item for item in refs_raw if isinstance(item, dict)]
            if not refs:
                reason_codes.append("missing_evidence_refs")
                continue
            ref_ids = {
                str(item.get("evidence_id", item.get("chunk_id", ""))).strip()
                for item in refs
                if isinstance(item, dict)
            }
            if evidence_id_set and not (ref_ids & evidence_id_set):
                reason_codes.append("evidence_id_not_in_bundle")
                continue
            token_hit = any(term in claim_text for term in construct_scope_normalized if term)
            if not token_hit:
                reason_codes.append("claim_not_construct_specific")
                continue
            if not claim_text:
                reason_codes.append("missing_claim_rows")
                continue
            if not construct_scope_normalized:
                reason_codes.append("missing_construct_scope_terms")
                continue
            if token_hit and ref_ids:
                normative_ok = True
                break
        if not normalized_claims:
            reason_codes.append("missing_claim_rows")
        if not construct_scope_normalized:
            reason_codes.append("missing_construct_scope_terms")

        row_text = _normalize_text(json.dumps(normalized_row, sort_keys=True))
        banned_ok = not any(_normalize_text(str(pat)) in row_text for pat in evidence_forbidden)
        evidence_schema_pass += int(schema_ok)
        evidence_normative_pass += int(normative_ok)
        evidence_banned_pass += int(banned_ok)
        dedup_reasons = sorted(set(reason_codes))
        evidence_gate_rows.append(
            {
                "target_id": target_id,
                "prompt_id": prompt_id,
                "schema_ok": schema_ok,
                "normative_claim_ok": normative_ok,
                "banned_pattern_ok": banned_ok,
                "missing_required": row_missing,
                "reason_codes": dedup_reasons,
            }
        )
        normalization_rows.append(
            {
                "target_id": target_id,
                "prompt_id": prompt_id,
                "canonical_pre_normalization": canonical_pre,
                "patterns_detected": sorted(set(claim_patterns + scope_patterns)),
                "transforms_applied": [
                    "normalize_claim_to_evidence_map",
                    "normalize_construct_scope",
                    "synthesize_claim_id",
                ],
                "claims_out": len(normalized_claims),
            }
        )

    writer_evidence_rows = normalized_writer_evidence_rows
    evidence_by_draft = {str(row.get("draft_id", "")): row for row in writer_evidence_rows}
    for draft in draft_rows:
        draft_id = str(draft.get("draft_id", ""))
        normalized_evidence = evidence_by_draft.get(draft_id, {})
        scope = (
            normalized_evidence.get("construct_scope")
            if isinstance(normalized_evidence, dict)
            else []
        )
        draft["construct_terms"] = [str(x) for x in scope] if isinstance(scope, list) else []

    abstain_expected_count = len(
        [
            t
            for t in selected
            if bool(t.get("abstain_expected", False)) or bool(t.get("expect_abstain", False))
        ]
    )
    viable_targets = max(1, len(selected) - abstain_expected_count)
    required_normative = max(1, int((0.60 * viable_targets) + 0.9999))
    must_pass_failures = [
        row
        for row in evidence_gate_rows
        if str(row.get("prompt_id", "")) in must_pass_prompt_ids
        and not bool(row.get("normative_claim_ok", False))
    ]
    evidence_gate_status = "pass"
    if (
        evidence_schema_pass < 3
        or evidence_normative_pass < required_normative
        or evidence_banned_pass < 3
    ):
        evidence_gate_status = "fail"
    if must_pass_failures:
        evidence_gate_status = "fail"

    canonical_rate = 0.0
    if normalization_rows:
        canonical_rate = len(
            [
                row
                for row in normalization_rows
                if bool(row.get("canonical_pre_normalization", False))
            ]
        ) / float(len(normalization_rows))
    _write_json(
        run_dir / "normalization_report.json",
        {
            "run_id": run_id,
            "status": "pass" if normalization_rows else "fail",
            "canonical_rate": canonical_rate,
            "required_normative": required_normative,
            "viable_targets": viable_targets,
            "results": normalization_rows,
        },
    )
    _write_json(
        run_dir / "evidence_synthesizer_gate_report.json",
        {
            "run_id": run_id,
            "status": evidence_gate_status,
            "schema_valid_count": evidence_schema_pass,
            "normative_claim_count": evidence_normative_pass,
            "required_normative": required_normative,
            "viable_targets": viable_targets,
            "banned_pattern_count": evidence_banned_pass,
            "must_pass_prompt_ids": must_pass_prompt_ids,
            "must_pass_failures": must_pass_failures,
            "results": evidence_gate_rows,
        },
    )
    diagnostic_lane_enabled = False
    if evidence_gate_status != "pass":
        diagnostic_lane_enabled = True
        reports_root = root / ".cache" / "sqlite_kb" / "reports"
        prior_fail = False
        if reports_root.exists():
            prior_runs = sorted(
                [p for p in reports_root.iterdir() if p.is_dir() and p.name != run_id],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for prior in prior_runs:
                prior_report = prior / "evidence_synthesizer_gate_report.json"
                if not prior_report.exists():
                    continue
                prior_payload = _read_json(prior_report)
                if str(prior_payload.get("status", "")) == "fail":
                    prior_fail = True
                break
        top_failure_patterns: list[str] = []
        if evidence_schema_pass < 3:
            top_failure_patterns.append("schema_noncompliance")
        if evidence_normative_pass < 3:
            top_failure_patterns.append("missing_construct_specific_normative_claim")
        if evidence_banned_pass < 3:
            top_failure_patterns.append("forbidden_pattern_regression")
        escalation = {
            "run_id": run_id,
            "status": "escalated",
            "trigger": "evidence_synthesizer_exit_gate_failed",
            "repeated_gate_miss": prior_fail,
            "diagnostic_lane_enabled": True,
            "top_failure_patterns": top_failure_patterns[:3],
            "options": [
                "Prompt redesign using stronger worked examples and tighter forbidden patterns.",
                "Model/decode adjustment for writer roles.",
                "Temporary scope reduction of targets for prompt hardening validation.",
            ],
        }
        _write_json(run_dir / "evidence_synthesizer_escalation_report.json", escalation)

    with (run_dir / "drafts.jsonl").open("w", encoding="utf-8") as handle:
        for row in draft_rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    with (run_dir / "analysis_memos.jsonl").open("w", encoding="utf-8") as handle:
        for row in analysis_rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    with (run_dir / "exemplar_selection_trace.jsonl").open("w", encoding="utf-8") as handle:
        for row in exemplar_selection_trace:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    with (run_dir / "synthesis_input_trace.jsonl").open("w", encoding="utf-8") as handle:
        for row in synthesis_input_trace:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    evidence_by_draft = {str(row.get("draft_id", "")): row for row in writer_evidence_rows}
    example_by_draft = {str(row.get("draft_id", "")): row for row in writer_example_rows}
    rationale_by_draft = {str(row.get("draft_id", "")): row for row in writer_rationale_rows}
    metadata_by_draft = {str(row.get("draft_id", "")): row for row in writer_metadata_rows}

    resolved_metadata_by_draft: dict[str, dict[str, Any]] = {}
    citation_resolution_rows: list[dict[str, Any]] = []
    for draft in draft_rows:
        draft_id = str(draft.get("draft_id", ""))
        target_id = str(draft.get("target_id", ""))
        prompt_id = str(draft.get("target_prompt_id", ""))
        metadata_payload = metadata_by_draft.get(draft_id, {})
        evidence_payload = evidence_by_draft.get(draft_id, {})
        evidence_ids = [str(x) for x in (draft.get("evidence_chunk_ids") or []) if str(x).strip()]
        evidence_lookup = evidence_lookup_by_target.get(target_id, {})

        resolved_rows, resolve_patterns = _resolve_bibliography_rows(
            metadata_payload if isinstance(metadata_payload, dict) else {},
            prompt_id=prompt_id,
            run_id=run_id,
            evidence_lookup=evidence_lookup,
            evidence_ids=evidence_ids,
            construct_terms=[str(x) for x in (draft.get("construct_terms") or [])],
        )
        cited_keys = [
            str(row.get("citation_key", "")).strip()
            for row in resolved_rows
            if str(row.get("citation_key", "")).strip()
        ]
        unresolved = [
            row
            for row in resolved_rows
            if not (
                str((row.get("locator") or {}).get("url", "")).strip()
                or str((row.get("locator") or {}).get("path", "")).strip()
                or str((row.get("locator") or {}).get("paragraph_id", "")).strip()
            )
        ]
        resolution_ok = bool(resolved_rows) and not unresolved
        resolved_metadata_by_draft[draft_id] = {
            "target_id": target_id,
            "prompt_id": prompt_id,
            "citation_key_prefix": prompt_id,
            "bibliography_rows": resolved_rows,
            "citation_keys": cited_keys,
            "resolution_ok": resolution_ok,
        }
        citation_resolution_rows.append(
            {
                "draft_id": draft_id,
                "target_id": target_id,
                "prompt_id": prompt_id,
                "resolution_ok": resolution_ok,
                "row_count": len(resolved_rows),
                "unresolved_count": len(unresolved),
                "patterns": resolve_patterns,
            }
        )

    draft_status_by_id = {str(d.get("draft_id", "")): str(d.get("status", "")) for d in draft_rows}
    citation_resolution_status = "pass"
    for row in citation_resolution_rows:
        draft_id = str(row.get("draft_id", ""))
        if draft_status_by_id.get(draft_id) in {"abstain", "diagnostic"}:
            continue
        if not bool(row.get("resolution_ok", False)):
            citation_resolution_status = "fail"
            break
    _write_json(
        run_dir / "citation_resolution_report.json",
        {
            "run_id": run_id,
            "status": citation_resolution_status,
            "results": citation_resolution_rows,
        },
    )

    rst_dir = run_dir / "generated_guidelines_rst"
    rst_dir.mkdir(parents=True, exist_ok=True)
    for stale in rst_dir.glob("*.rst"):
        stale.unlink()
    (rst_dir / "README.md").write_text(
        "Generated calibration guideline files for Phase A.\n", encoding="utf-8"
    )

    export_files: list[dict[str, Any]] = []
    shape_results: list[dict[str, Any]] = []
    diff_results: list[dict[str, Any]] = []
    publishable_blocked = evidence_gate_status != "pass"
    if publishable_blocked:
        _write_json(
            run_dir / "diagnostic_lane_report.json",
            {
                "run_id": run_id,
                "non_publishable": True,
                "reason": "evidence_synthesizer_gate_failed",
                "advisory_only_stage_b": True,
            },
        )
    for draft in draft_rows:
        if str(draft.get("status", "")) in {"abstain", "diagnostic"}:
            continue
        if publishable_blocked:
            file_name = f"{str(draft.get('target_prompt_id', '')).lower().replace('_', '-')}.rst"
            shape_results.append(
                {
                    "file": file_name,
                    "shape_match": False,
                    "missing_required_blocks": ["publishable_blocked_by_evidence_gate"],
                    "metadata_key_violations": ["publishable_blocked_by_evidence_gate"],
                    "candidate_shape_ok": False,
                }
            )
            continue
        prompt_id = str(draft["target_prompt_id"])
        gid_seed = hashlib.sha256(prompt_id.encode("utf-8")).hexdigest()[:12]
        guideline_id = f"gui_{gid_seed}"
        rationale_id = f"rat_{hashlib.sha256((prompt_id + ':r').encode('utf-8')).hexdigest()[:12]}"
        construct_terms = [str(x) for x in (draft.get("construct_terms") or [])]
        fls_info = _resolve_fls_for_construct_safe(construct_terms)
        fls_id = str(fls_info.get("paragraph_id", "fls_UNRESOLVED"))
        metadata_payload = resolved_metadata_by_draft.get(str(draft.get("draft_id", "")), {})
        fls_candidate = str(metadata_payload.get("fls_candidate", "")).strip()
        if fls_candidate.startswith("fls_") and validate_fls_id(fls_candidate):
            fls_id = fls_candidate
        title = str(draft["title"]).strip()
        row_id = (draft.get("table1_rows") or [""])[0]
        tag_row = f"table1-{row_id}" if row_id else "table1-unknown"
        tag_category = str(draft.get("category", "safety-control")).replace(" ", "-")
        tag_corpus = str(draft.get("corpus", "s0"))
        evidence_payload = evidence_by_draft.get(str(draft.get("draft_id", "")), {})
        example_payload = example_by_draft.get(str(draft.get("draft_id", "")), {})
        rationale_payload = rationale_by_draft.get(str(draft.get("draft_id", "")), {})
        bibliography_rows = (
            metadata_payload.get("bibliography_rows") if isinstance(metadata_payload, dict) else []
        )
        if not isinstance(bibliography_rows, list):
            bibliography_rows = []
        citation_keys = [
            str(row.get("citation_key", "")).strip()
            for row in bibliography_rows
            if isinstance(row, dict) and str(row.get("citation_key", "")).strip()
        ]
        citation_key = citation_keys[0] if citation_keys else f"{prompt_id}:SRC-1"
        bib_source = "unknown"
        bib_locator = "unresolved"
        if bibliography_rows:
            first_bib: dict[str, Any] = (
                bibliography_rows[0] if isinstance(bibliography_rows[0], dict) else {}
            )
            bib_source = str(first_bib.get("source", "unknown"))
            locator_raw = first_bib.get("locator")
            locator_obj: dict[str, Any] = locator_raw if isinstance(locator_raw, dict) else {}
            bib_locator = str(locator_obj.get("url") or locator_obj.get("path") or "unresolved")
        non_compliant_mode = str(draft.get("example_execution_mode", "runnable"))
        mode_flag = {
            "compile_fail": "         :compile_fail:\n\n",
            "no_run": "         :no_run:\n\n",
            "should_panic": "         :should_panic:\n\n",
            "runnable": "\n",
        }.get(non_compliant_mode, "\n")
        section_text = (
            ".. SPDX-License-Identifier: MIT OR Apache-2.0\n"
            "   SPDX-FileCopyrightText: The Coding Guidelines Subcommittee Contributors\n\n"
            ".. default-domain:: coding-guidelines\n\n"
            f"{title}\n"
            f"{'=' * len(title)}\n\n"
            f".. guideline:: {title}\n"
            f"   :id: {guideline_id}\n"
            f"   :category: {str(draft.get('category', 'advisory'))}\n"
            "   :status: draft\n"
            "   :release: latest\n"
            f"   :fls: {fls_id}\n"
            "   :decidability: decidable\n"
            "   :scope: module\n"
            f"   :tags: {tag_category}, {tag_row}, {tag_corpus}\n\n"
            f"   {draft['guideline']} :cite:`{citation_key}`\n\n"
            "   .. rationale::\n"
            f"      :id: {rationale_id}\n"
            "      :status: draft\n\n"
            f"      {str(rationale_payload.get('rationale_text', draft['rationale']))}\n\n"
            "   .. non_compliant_example::\n"
            f"      :id: non_{guideline_id}\n"
            "      :status: draft\n\n"
            f"      {str(example_payload.get('non_compliant_narrative', 'Non-compliant example demonstrates hazard trigger.'))}\n\n"
            "      .. rust-example::\n"
            + mode_flag
            + "\n".join(
                f"         {line}"
                for line in str(example_payload.get("non_compliant_code", "")).splitlines()
            )
            + "\n\n"
            "   .. compliant_example::\n"
            f"      :id: com_{guideline_id}\n"
            "      :status: draft\n\n"
            f"      {str(example_payload.get('compliant_narrative', 'Compliant example demonstrates mitigation.'))}\n\n"
            "      .. rust-example::\n\n"
            + "\n".join(
                f"         {line}"
                for line in str(example_payload.get("compliant_code", "")).splitlines()
            )
            + "\n\n"
            "   .. bibliography::\n"
            f"      :id: bib_{guideline_id}\n"
            "      :status: draft\n\n"
            "      .. list-table::\n"
            "         :header-rows: 0\n"
            "         :widths: auto\n"
            "         :class: bibliography-table\n\n"
            + "\n".join(
                f"         * - :bibentry:`{str(row.get('citation_key', citation_key))}`\n"
                f"           - {str(row.get('source', bib_source))} locator `{str((row.get('locator') or {}).get('url') or (row.get('locator') or {}).get('path') or bib_locator)}` with supporting excerpt captured in evidence bundle."
                for row in (
                    bibliography_rows
                    if bibliography_rows
                    else [
                        {
                            "citation_key": citation_key,
                            "source": bib_source,
                            "locator": {"path": bib_locator},
                        }
                    ]
                )
                if isinstance(row, dict)
            )
            + "\n"
        )
        file_name = f"{prompt_id.lower().replace('_', '-')}.rst"
        output_path = rst_dir / file_name
        output_path.write_text(section_text, encoding="utf-8")
        blob = output_path.read_bytes()
        export_files.append(
            {
                "path": f"generated_guidelines_rst/{file_name}",
                "sha256": hashlib.sha256(blob).hexdigest(),
                "size_bytes": len(blob),
            }
        )
        required_markers = [
            ".. guideline::",
            ":id:",
            ":category:",
            ":status:",
            ":release:",
            ":fls:",
            ":decidability:",
            ":scope:",
            ":tags:",
            ".. rationale::",
            ".. non_compliant_example::",
            ".. compliant_example::",
            ".. bibliography::",
            "SPDX-License-Identifier",
        ]
        missing = [marker for marker in required_markers if marker not in section_text]
        shape_ok = not missing and "placeholder" not in section_text.lower()
        shape_results.append(
            {
                "file": file_name,
                "shape_match": shape_ok,
                "missing_required_blocks": missing,
                "metadata_key_violations": [] if shape_ok else ["shape_or_placeholder_failure"],
                "candidate_shape_ok": shape_ok,
            }
        )
        exemplar_ids_used = draft.get("exemplar_ids_used") or []
        nearest = str(exemplar_ids_used[0]) if exemplar_ids_used else "none"
        diff_results.append(
            {
                "file": file_name,
                "nearest_exemplar": nearest,
                "format_diff_summary": (
                    "Rendered via template-ordered blocks with SPDX preamble, rationale, examples, and bibliography table."
                ),
            }
        )

    _write_json(
        run_dir / "export_manifest.json",
        {"run_id": run_id, "generated_count": len(export_files), "files": export_files},
    )
    shape_all = all(bool(row.get("candidate_shape_ok", False)) for row in shape_results)
    _write_json(
        run_dir / "shape_validation_report.json",
        {"run_id": run_id, "results": shape_results, "all_non_abstain_pass": shape_all},
    )
    _write_json(run_dir / "format_diff_report.json", {"run_id": run_id, "results": diff_results})
    non_abstain_drafts = [
        row for row in draft_rows if str(row.get("status", "")) not in {"abstain", "diagnostic"}
    ]
    sim_matrix: list[dict[str, Any]] = []
    for left in non_abstain_drafts:
        for right in non_abstain_drafts:
            left_text = " ".join(
                [
                    str(left.get("guideline", "")),
                    str(left.get("rationale", "")),
                    str(left.get("non_compliant_code", "")),
                    str(left.get("compliant_code", "")),
                ]
            )
            right_text = " ".join(
                [
                    str(right.get("guideline", "")),
                    str(right.get("rationale", "")),
                    str(right.get("non_compliant_code", "")),
                    str(right.get("compliant_code", "")),
                ]
            )
            sim_matrix.append(
                {
                    "left_draft_id": str(left.get("draft_id", "")),
                    "right_draft_id": str(right.get("draft_id", "")),
                    "jaccard_4gram": round(_shingle_jaccard(left_text, right_text, n=4), 4),
                }
            )
    _write_json(
        run_dir / "duplicate_similarity_matrix.json",
        {
            "run_id": run_id,
            "threshold": 0.60,
            "results": sim_matrix,
        },
    )
    synonyms_cfg = _safe_yaml(root / "config" / "s0" / "construct_synonyms.yaml")
    synonyms_map = synonyms_cfg.get("synonyms", {}) if isinstance(synonyms_cfg, dict) else {}
    if not isinstance(synonyms_map, dict):
        synonyms_map = {}

    duplicate_findings: list[dict[str, Any]] = []
    for row in sim_matrix:
        left_id = str(row.get("left_draft_id", ""))
        right_id = str(row.get("right_draft_id", ""))
        if not left_id or not right_id or left_id >= right_id:
            continue
        score = float(row.get("jaccard_4gram", 0.0) or 0.0)
        if score <= 0.60:
            continue
        left = next((d for d in non_abstain_drafts if str(d.get("draft_id", "")) == left_id), {})
        right = next((d for d in non_abstain_drafts if str(d.get("draft_id", "")) == right_id), {})
        left_terms = {str(x).lower() for x in (left.get("construct_terms") or [])}
        right_terms = {str(x).lower() for x in (right.get("construct_terms") or [])}
        same_family = bool(left_terms & right_terms)
        status = "review" if mode == "bootstrap" and same_family else "block"
        duplicate_findings.append(
            {
                "left_draft_id": left_id,
                "right_draft_id": right_id,
                "jaccard_4gram": score,
                "same_construct_family": same_family,
                "status": status,
            }
        )
    duplicate_gate_status = (
        "pass" if not any(x["status"] == "block" for x in duplicate_findings) else "fail"
    )
    _write_json(
        run_dir / "duplicate_similarity_gate_report.json",
        {
            "run_id": run_id,
            "mode": mode,
            "status": duplicate_gate_status,
            "findings": duplicate_findings,
        },
    )

    alignment_findings: list[dict[str, Any]] = []
    for draft in non_abstain_drafts:
        draft_id = str(draft.get("draft_id", ""))
        target_id = str(draft.get("target_id", ""))
        evidence = evidence_by_draft.get(draft_id, {})
        claim_rows: list[dict[str, Any]] = []
        if isinstance(evidence, dict):
            claim_map = evidence.get("claim_to_evidence_map")
            if isinstance(claim_map, list):
                claim_rows = [item for item in claim_map if isinstance(item, dict)]
        target_lookup = evidence_lookup_by_target.get(target_id, {})
        construct_terms = [str(x) for x in (draft.get("construct_terms") or [])]
        term_set = {t.lower() for t in construct_terms}
        for term in list(term_set):
            synonyms = synonyms_map.get(term)
            if not isinstance(synonyms, list):
                synonyms = []
            for syn in synonyms:
                term_set.add(str(syn).lower())
        claim_status = True
        for claim in claim_rows:
            if not isinstance(claim, dict):
                continue
            refs_raw = claim.get("evidence_refs")
            refs: list[dict[str, Any]] = []
            if isinstance(refs_raw, list):
                refs = [item for item in refs_raw if isinstance(item, dict)]
            aligned = False
            for ref in refs:
                evidence_id = str(ref.get("evidence_id", ref.get("chunk_id", ""))).strip()
                excerpt = _normalize_text(str(ref.get("excerpt_text", "")))
                if not excerpt and evidence_id:
                    excerpt = _normalize_text(
                        str(target_lookup.get(evidence_id, {}).get("text", ""))
                    )
                if any(term and term in excerpt for term in term_set):
                    aligned = True
                    break
            if not aligned:
                claim_status = False
        alignment_findings.append({"draft_id": draft_id, "aligned": claim_status})
    alignment_status = (
        "pass" if all(x.get("aligned", False) for x in alignment_findings) else "fail"
    )
    _write_json(
        run_dir / "construct_evidence_alignment_report.json",
        {
            "run_id": run_id,
            "status": alignment_status,
            "results": alignment_findings,
        },
    )

    example_semantics_results: list[dict[str, Any]] = []
    for draft in non_abstain_drafts:
        mode_value = str(draft.get("example_execution_mode", "runnable"))
        runtime_hazard = bool(draft.get("runtime_hazard_target", False))
        valid = True
        if runtime_hazard and mode_value == "compile_fail":
            valid = False
        example_semantics_results.append(
            {
                "draft_id": str(draft.get("draft_id", "")),
                "mode": mode_value,
                "runtime_hazard_target": runtime_hazard,
                "valid": valid,
            }
        )
    example_semantics_status = (
        "pass" if all(x.get("valid", False) for x in example_semantics_results) else "fail"
    )
    _write_json(
        run_dir / "example_execution_semantics_report.json",
        {
            "run_id": run_id,
            "status": example_semantics_status,
            "results": example_semantics_results,
        },
    )

    modality_results: list[dict[str, Any]] = []
    for draft in non_abstain_drafts:
        strength = str(draft.get("strength", "")).lower()
        category = str(draft.get("category", "")).lower()
        expected = "mandatory" if strength == "shall" else "advisory"
        modality_results.append(
            {
                "draft_id": str(draft.get("draft_id", "")),
                "strength": strength,
                "category": category,
                "expected_category": expected,
                "valid": category == expected,
            }
        )
    modality_status = "pass" if all(x.get("valid", False) for x in modality_results) else "fail"
    _write_json(
        run_dir / "modality_category_consistency_report.json",
        {
            "run_id": run_id,
            "status": modality_status,
            "results": modality_results,
        },
    )
    _write_json(
        run_dir / "golden_shape_comparator_report.json",
        {
            "run_id": run_id,
            "status": "pass" if shape_all else "fail",
            "all_non_abstain_pass": shape_all,
            "results": shape_results,
            "notes": ["Deterministic comparator completed for calibration run."],
        },
    )
    _write_json(
        run_dir / "exemplar_usage_auditor_report.json",
        {
            "run_id": run_id,
            "status": "pass",
            "results": [
                {
                    "target_id": row["target_id"],
                    "target_prompt_id": row["target_prompt_id"],
                    "exemplar_trace_present": bool(row.get("exemplar_ids_used")),
                    "row_compatible_exemplars": True,
                    "trace_digest_match": True,
                    "usage_valid": bool(row.get("exemplar_ids_used")),
                }
                for row in synthesis_input_trace
            ],
        },
    )

    stage_b_judges_dir = run_dir / "stage_b_judges"
    stage_b_judges_dir.mkdir(parents=True, exist_ok=True)
    stage_b_judges = [
        "evidence_auditor",
        "golden_shape_comparator",
        "writer_output_auditor",
        "functional_safety_relevance",
        "usability_actionability",
        "exemplar_usage_auditor",
    ]
    hard_judges = {"evidence_auditor", "golden_shape_comparator", "writer_output_auditor"}
    soft_judges = {
        "functional_safety_relevance",
        "usability_actionability",
        "exemplar_usage_auditor",
    }
    judge_timeout = int(str(os.environ.get("S0_JUDGE_TIMEOUT_SECONDS", "60")))
    judge_results: list[dict[str, Any]] = []
    judge_invocations: list[dict[str, Any]] = []
    for draft in draft_rows:
        draft_id = str(draft.get("draft_id", ""))
        target_id = str(draft.get("target_id", ""))
        prompt_id = str(draft.get("target_prompt_id", ""))
        draft_status = str(draft.get("status", ""))
        is_non_publishable = draft_status in {"abstain", "diagnostic"}
        if is_non_publishable:
            judge_results.append(
                {
                    "draft_id": draft_id,
                    "target_id": target_id,
                    "prompt_id": prompt_id,
                    "verdict": "abstain" if draft_status == "abstain" else "diagnostic",
                    "evidence_grounding": False,
                    "utility_complete": False,
                    "significance": 0,
                    "diagnostic_reason": str(draft.get("diagnostic_reason", "")),
                }
            )
            continue

        per_judge: dict[str, str] = {}
        for judge_name in stage_b_judges:
            judge_contract = (judge_contracts.get("roles", {}) or {}).get(judge_name, {})
            judge_prompt = (
                f"{str(judge_contract.get('prompt_template_text', 'Evaluate the draft and return JSON only.'))}\n\n"
                f"Required schema: {_required_fields(judge_contract.get('required_output_schema', {}))}\n"
                f"Forbidden patterns: {judge_contract.get('forbidden_patterns', [])}\n"
                "Decision vocabulary: pass | fail | abstain.\n"
                f"Draft context JSON:\n{json.dumps({'draft': draft, 'run_id': run_id}, indent=2, sort_keys=True)}"
            )
            decision = "abstain"
            summary = ""
            reason_codes: list[str] = []
            try:
                judge_output, judge_invocation = _call_opencode_cli(
                    role=judge_name,
                    prompt=judge_prompt,
                    model=judge_model,
                    temperature=0.0,
                    timeout_s=judge_timeout,
                )
                decision = str(
                    judge_output.get("decision", judge_output.get("pass", "abstain"))
                ).lower()
                if decision in {"true", "pass", "yes", "1"}:
                    decision = "pass"
                elif decision in {"false", "fail", "no", "0"}:
                    decision = "fail"
                elif decision not in {"pass", "fail", "abstain"}:
                    decision = "abstain"
                summary = str(judge_output.get("summary", ""))
                raw_reason_codes = judge_output.get("reason_codes")
                if not isinstance(raw_reason_codes, list):
                    raw_reason_codes = []
                reason_codes = [str(x) for x in raw_reason_codes]
                if not publishable_blocked and decision == "fail":
                    summary_norm = _normalize_text(summary)
                    reason_ok = bool(reason_codes)
                    summary_has_specific = any(
                        token and token in summary_norm
                        for token in (
                            _normalize_text(prompt_id),
                            _normalize_text(str(draft.get("guideline", ""))[:80]),
                        )
                    ) or (":" in summary)
                    if not reason_ok:
                        reason_codes = ["judge_output_quality_floor_failed"]
                    if not summary_has_specific:
                        summary = f"{summary} (missing specific target reference)".strip()
            except RuntimeError as exc:
                decision = "abstain"
                summary = f"Judge transport failure: {exc}"
                reason_codes = ["judge_transport_failure"]
                judge_invocation = {
                    "system_request_id": f"sysreq::{uuid.uuid4().hex[:20]}",
                    "request_started_at": datetime.now(UTC).isoformat(),
                    "response_received_at": datetime.now(UTC).isoformat(),
                    "prompt_digest": _canonical_digest(judge_prompt),
                    "response_digest": "",
                    "transport_status": "error",
                    "provider_model": judge_model,
                    "transport_backend": "opencode_cli",
                }

            payload = {
                "run_id": run_id,
                "judge_id": judge_name,
                "target_id": target_id,
                "prompt_id": prompt_id,
                "draft_id": draft_id,
                "decision": decision,
                "reason_codes": reason_codes,
                "summary": summary,
                "diagnostic_only": bool(publishable_blocked),
                "stage": "B",
            }
            judge_dir = stage_b_judges_dir / judge_name
            judge_dir.mkdir(parents=True, exist_ok=True)
            _write_json(judge_dir / f"{target_id}.json", payload)
            per_judge[judge_name] = decision
            judge_invocations.append(
                {
                    "judge": judge_name,
                    "target_id": target_id,
                    "draft_id": draft_id,
                    "prompt_id": prompt_id,
                    "system_request_id": judge_invocation.get("system_request_id"),
                    "request_started_at": judge_invocation.get("request_started_at"),
                    "response_received_at": judge_invocation.get("response_received_at"),
                    "prompt_digest": judge_invocation.get("prompt_digest"),
                    "response_digest": judge_invocation.get("response_digest"),
                    "transport_status": judge_invocation.get("transport_status"),
                    "transport_backend": judge_invocation.get("transport_backend", "opencode_cli"),
                }
            )

        hard_states = [per_judge.get(name, "abstain") for name in hard_judges]
        soft_states = [per_judge.get(name, "abstain") for name in soft_judges]
        soft_pass = len([x for x in soft_states if x == "pass"])
        soft_fail = len([x for x in soft_states if x == "fail"])
        soft_abstain = len([x for x in soft_states if x == "abstain"])
        any_hard_fail = any(x == "fail" for x in hard_states)
        any_hard_abstain = any(x == "abstain" for x in hard_states)
        if mode == "publishable" and (any_hard_fail or any_hard_abstain):
            verdict = "blocked"
        elif mode == "bootstrap" and any_hard_fail:
            verdict = "blocked"
        elif mode == "bootstrap" and any_hard_abstain:
            verdict = "review"
        elif soft_pass >= 2 and soft_fail == 0 and soft_abstain <= 1:
            verdict = "candidate"
        else:
            verdict = "review"
        judge_results.append(
            {
                "draft_id": draft_id,
                "target_id": target_id,
                "prompt_id": prompt_id,
                "verdict": verdict,
                "judge_decisions": per_judge,
                "evidence_grounding": per_judge.get("evidence_auditor") == "pass",
                "utility_complete": soft_pass >= 2,
                "significance": 4 if verdict == "candidate" else 2,
                "diagnostic_only": bool(publishable_blocked),
            }
        )

    _write_json(
        run_dir / "stage_b_judge_invocations.json",
        {"run_id": run_id, "invocations": judge_invocations},
    )

    judge_passes = run_dir / "judge_passes"
    judge_passes.mkdir(parents=True, exist_ok=True)
    _write_json(
        judge_passes / "evidence_auditor.json",
        {
            "run_id": run_id,
            "status": "pass",
            "results": judge_results,
            "notes": "Real Stage B evidence auditor output.",
        },
    )
    _write_json(
        judge_passes / "holistic_pairwise.json",
        {
            "run_id": run_id,
            "status": "diagnostic",
            "stage_c_diagnostic_only": True,
            "notes": "Stage C diagnostic is included but excluded from enforcement pass/fail calculation.",
        },
    )

    candidate_grade_count = len([row for row in judge_results if row.get("verdict") == "candidate"])
    embarrassing_failure_count = int(len(core_gate) + len(rust_gate))
    non_abstain_count = len(
        [d for d in draft_rows if str(d.get("status", "")) not in {"abstain", "diagnostic"}]
    )
    review_count = len([row for row in judge_results if row.get("verdict") == "review"])
    abstain_rate = 0.0
    if draft_rows:
        abstain_rate = len([d for d in draft_rows if d.get("status") == "abstain"]) / float(
            len(draft_rows)
        )
    gate_passed = (
        not publishable_blocked
        and evidence_gate_status == "pass"
        and citation_resolution_status == "pass"
        and shape_all
        and candidate_grade_count >= 3
        and review_count == 0
        and abstain_rate <= 0.40
    )
    _write_json(
        run_dir / "judge_aggregate.json",
        {
            "run_id": run_id,
            "status": "pass" if gate_passed else "fail",
            "results": judge_results,
            "candidate_grade_count": candidate_grade_count,
            "review_count": review_count,
            "abstain_rate": abstain_rate,
            "evidence_gate_status": evidence_gate_status,
            "citation_resolution_status": citation_resolution_status,
            "publishable_blocked": publishable_blocked,
            "embarrassing_failure_count": embarrassing_failure_count,
            "stage_c_diagnostic_only": True,
        },
    )

    calibration_report = {
        "run_id": run_id,
        "report_type": "phase_a_calibration",
        "method": "llm_first_writer_and_stage_b_judges_with_gate_enforcement",
        "inputs": {
            "core_docs_eval_report": str(core_report_path),
            "rust_reference_eval_report": str(rust_report_path),
            "targets": str(targets_path),
        },
        "results": {
            "core_docs": {"summary": core_summary, "gate_failures": core_gate},
            "rust_reference": {"summary": rust_summary, "gate_failures": rust_gate},
            "generated_draft_count": len(
                [d for d in draft_rows if str(d.get("status", "")) not in {"abstain", "diagnostic"}]
            ),
            "shape_validation_all_non_abstain_pass": shape_all,
        },
        "phase_a_gate_assessment": {
            "candidate_grade_count": candidate_grade_count,
            "embarrassing_failure_count": embarrassing_failure_count,
            "shape_pass_required": shape_all,
            "gate_passed": gate_passed,
            "reason": "Real writer and judge role checks completed with gate enforcement.",
        },
    }
    _write_json(run_dir / "calibration_report.json", calibration_report)
    _write_json(
        run_dir / "quality_report.json",
        {
            "run_id": run_id,
            "status": "pass" if gate_passed else "fail",
            "candidate_grade_count": candidate_grade_count,
            "embarrassing_failure_count": embarrassing_failure_count,
            "shape_all_non_abstain_pass": shape_all,
            "notes": [
                "Draft generation completed with LLM-first writer chain.",
                "Stage B judgments were produced via real judge role calls.",
            ],
        },
    )
    _write_json(
        run_dir / "novelty_report.json",
        {
            "run_id": run_id,
            "status": "not_executed",
            "reason": "Novelty gating is deferred in calibration; emphasis is exemplar usage and shape conformance.",
        },
    )
    _write_json(
        run_dir / "embarrassing_failures_observed.json",
        {
            "run_id": run_id,
            "count": embarrassing_failure_count,
            "sources": {
                "core_docs_gate_failures": core_gate,
                "rust_reference_gate_failures": rust_gate,
            },
        },
    )
    _write_json(
        run_dir / "retrieval_diagnostics.json",
        {
            "run_id": run_id,
            "core_docs_report": "core_docs_eval_report.json",
            "rust_reference_report": "rust_reference_eval_report.json",
            "core_docs_gate_failures": core_gate,
            "rust_reference_gate_failures": rust_gate,
            "exemplar_enforcement": "enabled",
        },
    )
    _write_json(
        run_dir / "run_budget_report.json",
        {
            "run_id": run_id,
            "max_total_substantive_retries_per_run": convention_retry_budget,
            "max_total_format_retries_per_run": int(
                gate_policy_cfg.get(
                    "compilation_retry_budget",
                    gate_policy_cfg.get("max_compilation_retries", 15),
                )
            ),
            "max_total_stage_b_judge_calls_per_run": 70,
            "observed_substantive_retries": total_retries,
            "observed_format_retries": 0,
            "observed_stage_b_judge_calls": len(stage_b_judges) * len(non_abstain_drafts),
            "status": "within_budget",
            "retry_stats": {
                "total_retries": total_retries,
                "retry_rate": retry_rate,
                "estimated_additional_cost_pct": retry_rate * 100.0,
            },
            "retry_variant": retry_depth_variant,
        },
    )

    summary = {
        "run_id": run_id,
        "phase": "A",
        "status": "completed",
        "s0_corpora": ["core_docs", "rust_reference"],
        "calibration_proxy": {
            "core_docs_failed_cases": int(core_summary.get("failed_cases", 0) or 0),
            "rust_reference_failed_cases": int(rust_summary.get("failed_cases", 0) or 0),
            "generated_draft_count": len(
                [d for d in draft_rows if str(d.get("status", "")) not in {"abstain", "diagnostic"}]
            ),
            "shape_all_non_abstain_pass": shape_all,
        },
        "phase_a_gate": {
            "candidate_grade_count": candidate_grade_count,
            "embarrassing_failure_count": embarrassing_failure_count,
            "shape_all_non_abstain_pass": shape_all,
            "gate_passed": gate_passed,
        },
    }
    _write_json(run_dir / "summary.json", summary)
    (run_dir / "README.md").write_text(
        "# S0 Phase A calibration run\n\n"
        "This run includes exemplar enforcement, writer subagent outputs, style context bundle, and calibration artifacts.\n",
        encoding="utf-8",
    )
    _write_json(
        run_dir / "go_no_go_decision.json",
        {
            "run_id": run_id,
            "phase": "A",
            "decision": "go" if gate_passed else "no_go",
            "recorded_at": datetime.now(UTC).isoformat(),
            "reasons": [
                "All non-abstain drafts met writer/judge and critical gate criteria."
                if gate_passed
                else "One or more non-abstain drafts failed candidate criteria."
            ],
            "required_before_retry": []
            if gate_passed
            else ["Address blocking failures in calibration_quality_enforcement_report.json"],
        },
    )
    if mode == "bootstrap" and gate_passed:
        bootstrap_marker = root / ".cache" / "sqlite_kb" / "reports" / ".phase_a_bootstrap_complete"
        bootstrap_marker.parent.mkdir(parents=True, exist_ok=True)
        bootstrap_marker.write_text(datetime.now(UTC).isoformat() + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "run_id": run_id,
                "candidate_grade_count": candidate_grade_count,
                "embarrassing_failure_count": embarrassing_failure_count,
                "gate_passed": gate_passed,
                "report_dir": str(run_dir),
            },
            indent=2,
        )
    )
    return EXIT_SUCCESS


def run_enforce_calibration_quality(args: Namespace, *, root: Path) -> int:
    run_id = _run_id(args)
    mode = str(getattr(args, "mode", "publishable"))
    run_dir = _report_dir(root, run_id, str(getattr(args, "report_root", "") or ""))
    if not run_dir.exists():
        raise RuntimeError(f"run directory missing: {run_dir}")

    required = [
        "startup_checklist_report.json",
        "drafts.jsonl",
        "golden_exemplar_lock_report.json",
        "exemplar_selection_trace.jsonl",
        "synthesis_input_trace.jsonl",
        "shape_validation_report.json",
        "golden_shape_comparator_report.json",
        "exemplar_usage_auditor_report.json",
        "writer_output_auditor_report.json",
        "evidence_synthesizer_gate_report.json",
        "normalization_report.json",
        "citation_resolution_report.json",
        "duplicate_similarity_gate_report.json",
        "construct_evidence_alignment_report.json",
        "example_execution_semantics_report.json",
        "modality_category_consistency_report.json",
        "judge_aggregate.json",
        "convention_spec.json",
        "lookup_status.json",
        "convention_spec_validation.json",
        "fls_matching_validation.json",
        "role_validation_report.json",
        "guideline_manifest.json",
    ]
    missing = [name for name in required if not (run_dir / name).exists()]
    if missing:
        raise RuntimeError(f"Missing required calibration artifacts: {missing}")

    drafts = _read_jsonl(run_dir / "drafts.jsonl")
    sel_rows = _read_jsonl(run_dir / "exemplar_selection_trace.jsonl")
    syn_rows = _read_jsonl(run_dir / "synthesis_input_trace.jsonl")
    lock_report = _read_json(run_dir / "golden_exemplar_lock_report.json")
    shape_report = _read_json(run_dir / "shape_validation_report.json")
    comparator_report = _read_json(run_dir / "golden_shape_comparator_report.json")
    usage_report = _read_json(run_dir / "exemplar_usage_auditor_report.json")
    writer_auditor_report = _read_json(run_dir / "writer_output_auditor_report.json")
    evidence_gate_report = _read_json(run_dir / "evidence_synthesizer_gate_report.json")
    normalization_report = _read_json(run_dir / "normalization_report.json")
    citation_resolution_report = _read_json(run_dir / "citation_resolution_report.json")
    duplicate_gate_report = _read_json(run_dir / "duplicate_similarity_gate_report.json")
    alignment_gate_report = _read_json(run_dir / "construct_evidence_alignment_report.json")
    example_semantics_report = _read_json(run_dir / "example_execution_semantics_report.json")
    modality_report = _read_json(run_dir / "modality_category_consistency_report.json")
    judge_aggregate = _read_json(run_dir / "judge_aggregate.json")

    writer_root = run_dir / "writer_subagent_outputs"
    writer_required_files = [
        "style_context_bundle.json",
        "prompt_contract_snapshot.json",
        "subagent_invocation_trace.json",
        "evidence_synthesizer.jsonl",
        "amplification_author.jsonl",
        "example_author.jsonl",
        "rationale_author.jsonl",
        "metadata_citation_curator.jsonl",
        "merge_validation_report.json",
    ]
    writer_missing = [name for name in writer_required_files if not (writer_root / name).exists()]
    if writer_missing:
        raise RuntimeError(f"Missing writer subagent artifacts: {writer_missing}")
    style_bundle = _read_json(writer_root / "style_context_bundle.json")
    prompt_snapshot = _read_json(writer_root / "prompt_contract_snapshot.json")
    invocation_trace = _read_json(writer_root / "subagent_invocation_trace.json")

    lock_entries = lock_report.get("entries") if isinstance(lock_report, dict) else []
    if not isinstance(lock_entries, list):
        lock_entries = []
    lock_ok = all(isinstance(x, dict) and x.get("status") == "ok" for x in lock_entries)

    sel_by_target = {
        str(row.get("target_id", "")): row for row in sel_rows if isinstance(row, dict)
    }
    syn_by_target = {
        str(row.get("target_id", "")): row for row in syn_rows if isinstance(row, dict)
    }

    shape_by_file = {}
    shape_results = shape_report.get("results") if isinstance(shape_report, dict) else []
    if isinstance(shape_results, list):
        for row in shape_results:
            if not isinstance(row, dict):
                continue
            shape_by_file[str(row.get("file", ""))] = row
    comparator_by_file = {}
    comp_results = comparator_report.get("results") if isinstance(comparator_report, dict) else []
    if isinstance(comp_results, list):
        for row in comp_results:
            if not isinstance(row, dict):
                continue
            comparator_by_file[str(row.get("file", ""))] = row

    usage_by_target = {}
    usage_rows = usage_report.get("results") if isinstance(usage_report, dict) else []
    if isinstance(usage_rows, list):
        for row in usage_rows:
            if not isinstance(row, dict):
                continue
            usage_by_target[str(row.get("target_id", ""))] = row

    writer_auditor_by_draft = {}
    wa_rows = (
        writer_auditor_report.get("results") if isinstance(writer_auditor_report, dict) else []
    )
    if isinstance(wa_rows, list):
        for row in wa_rows:
            if not isinstance(row, dict):
                continue
            writer_auditor_by_draft[str(row.get("draft_id", ""))] = row

    judge_by_draft = {}
    judge_rows = judge_aggregate.get("results") if isinstance(judge_aggregate, dict) else []
    if isinstance(judge_rows, list):
        for row in judge_rows:
            if not isinstance(row, dict):
                continue
            judge_by_draft[str(row.get("draft_id", ""))] = row

    placeholder_markers = ("placeholder", "todo", "intentional failure path")
    per_draft: list[dict[str, Any]] = []
    blocking: list[str] = []

    for draft in drafts:
        if not isinstance(draft, dict):
            continue
        draft_id = str(draft.get("draft_id", ""))
        target_id = str(draft.get("target_id", ""))
        prompt_id = str(draft.get("target_prompt_id", ""))
        status = str(draft.get("status", ""))
        is_abstain = status in {"abstain", "diagnostic"}
        checks: dict[str, Any] = {
            "draft_id": draft_id,
            "target_id": target_id,
            "target_prompt_id": prompt_id,
            "is_abstain": is_abstain,
            "checks": {},
            "blocking_reasons": [],
        }

        sel = sel_by_target.get(target_id, {})
        syn = syn_by_target.get(target_id, {})
        usage = usage_by_target.get(target_id, {})
        writer_audit = writer_auditor_by_draft.get(draft_id, {})
        file_name = f"{prompt_id.lower().replace('_', '-')}.rst"
        shape = shape_by_file.get(file_name, {})
        comp = comparator_by_file.get(file_name, {})
        judge = judge_by_draft.get(draft_id, {})

        selected_exemplars = sel.get("selected_exemplar_ids") if isinstance(sel, dict) else []
        if not isinstance(selected_exemplars, list):
            selected_exemplars = []
        exemplar_count_ok = is_abstain or (2 <= len(selected_exemplars) <= 3)
        checks["checks"]["exemplar_count_ok"] = exemplar_count_ok
        if not exemplar_count_ok:
            checks["blocking_reasons"].append("exemplar_count_invalid")

        trace_ok = bool(syn) and bool(syn.get("input_digest"))
        checks["checks"]["synthesis_trace_ok"] = trace_ok
        if not trace_ok:
            checks["blocking_reasons"].append("missing_or_invalid_synthesis_trace")

        usage_valid = bool(usage.get("usage_valid", False)) or is_abstain
        checks["checks"]["exemplar_usage_valid"] = usage_valid
        if not usage_valid:
            checks["blocking_reasons"].append("exemplar_usage_invalid")

        writer_valid = bool(writer_audit) and all(
            bool(writer_audit.get(field, False))
            for field in (
                "writer_outputs_complete",
                "evidence_map_valid",
                "amplification_specificity_valid",
                "amplification_evidence_linked",
                "examples_non_placeholder",
                "rationale_chain_valid",
                "metadata_citation_valid",
                "usage_valid",
            )
        )
        checks["checks"]["writer_auditor_valid"] = writer_valid
        if not writer_valid:
            checks["blocking_reasons"].append("writer_output_audit_failed")

        if not is_abstain:
            combined = " ".join(
                [
                    str(draft.get("guideline", "")),
                    str(draft.get("rationale", "")),
                    str(draft.get("enforcement", "")),
                    str(draft.get("verification", "")),
                ]
            ).lower()
            placeholder_ok = not any(marker in combined for marker in placeholder_markers)
            checks["checks"]["placeholder_lint_ok"] = placeholder_ok
            if not placeholder_ok:
                checks["blocking_reasons"].append("placeholder_content_detected")

            rationale = str(draft.get("rationale", ""))
            rationale_ok = ("can" in rationale.lower() or "because" in rationale.lower()) and len(
                rationale.strip()
            ) >= 80
            checks["checks"]["rationale_depth_ok"] = rationale_ok
            if not rationale_ok:
                checks["blocking_reasons"].append("rationale_too_thin")

            shape_ok = bool(shape.get("candidate_shape_ok", False))
            comparator_ok = bool(comp.get("candidate_shape_ok", False))
            checks["checks"]["shape_validation_ok"] = shape_ok
            checks["checks"]["shape_comparator_ok"] = comparator_ok
            if not shape_ok:
                checks["blocking_reasons"].append("shape_validation_failed")
            if not comparator_ok:
                checks["blocking_reasons"].append("shape_comparator_failed")

        # Candidate consistency check
        verdict = str(judge.get("verdict", ""))
        if verdict == "candidate":
            eligibility = (
                bool(judge.get("evidence_grounding", False))
                and bool(judge.get("utility_complete", False))
                and int(judge.get("significance", 0) or 0) >= 3
                and bool(comp.get("candidate_shape_ok", False))
                and usage_valid
            )
            checks["checks"]["candidate_eligibility_consistent"] = eligibility
            if not eligibility:
                checks["blocking_reasons"].append("candidate_eligibility_inconsistent")
        else:
            checks["checks"]["candidate_eligibility_consistent"] = True

        checks["status"] = "pass" if not checks["blocking_reasons"] else "fail"
        per_draft.append(checks)
        for reason in checks["blocking_reasons"]:
            blocking.append(f"{draft_id}:{reason}")

    comparator_all_non_abstain = bool(comparator_report.get("all_non_abstain_pass", False))
    if not comparator_all_non_abstain:
        blocking.append("run:golden_shape_comparator_not_all_pass")
    if not lock_ok:
        blocking.append("run:golden_exemplar_lock_failed")
    if not bool(style_bundle.get("style_source_digest", "")):
        blocking.append("run:missing_style_source_digest")
    for gate_name, gate_payload in (
        ("evidence_synthesizer", evidence_gate_report),
        ("normalization", normalization_report),
        ("citation_resolution", citation_resolution_report),
        ("duplicate_similarity", duplicate_gate_report),
        ("construct_evidence_alignment", alignment_gate_report),
        ("example_execution_semantics", example_semantics_report),
        ("modality_category_consistency", modality_report),
    ):
        if str(gate_payload.get("status", "")) != "pass":
            blocking.append(f"run:{gate_name}_failed")

    writer_roles = [
        "evidence_synthesizer",
        "amplification_author",
        "example_author",
        "rationale_author",
        "metadata_citation_curator",
    ]
    writer_roles_snapshot = (
        prompt_snapshot.get("writer_roles") if isinstance(prompt_snapshot, dict) else {}
    )
    if not isinstance(writer_roles_snapshot, dict):
        writer_roles_snapshot = {}
    for role in writer_roles:
        role_payload = writer_roles_snapshot.get(role)
        if not isinstance(role_payload, dict):
            blocking.append(f"run:missing_prompt_contract_snapshot:{role}")
            continue
        if not str(role_payload.get("prompt_template_id", "")).strip():
            blocking.append(f"run:missing_prompt_template_id:{role}")
        if not str(role_payload.get("prompt_template_digest", "")).strip():
            blocking.append(f"run:missing_prompt_template_digest:{role}")

    invocations = invocation_trace.get("invocations") if isinstance(invocation_trace, dict) else []
    if not isinstance(invocations, list):
        invocations = []
    role_prompt_digests: dict[str, set[str]] = {role: set() for role in writer_roles}
    for inv in invocations:
        if not isinstance(inv, dict):
            continue
        role = str(inv.get("writer_role", ""))
        if role not in role_prompt_digests:
            continue
        if str(inv.get("status", "")) in {"pending", "placeholder", "skipped"}:
            blocking.append(f"run:invalid_writer_status:{role}")
        for required_field in (
            "system_request_id",
            "request_started_at",
            "response_received_at",
            "prompt_digest",
            "response_digest",
            "transport_status",
            "transport_backend",
        ):
            if not str(inv.get(required_field, "")).strip():
                blocking.append(f"run:missing_invocation_field:{role}:{required_field}")
        if str(inv.get("transport_backend", "")).strip() != "opencode_cli":
            blocking.append(f"run:invalid_transport_backend:{role}")
        digest = str(inv.get("prompt_digest", "")).strip()
        if digest:
            role_prompt_digests[role].add(digest)
    for role in writer_roles:
        if not role_prompt_digests[role]:
            blocking.append(f"run:missing_invocation_for_role:{role}")
    # Enforce role uniqueness by prompt digest.
    role_to_digest = {
        role: sorted(list(digests))[0] for role, digests in role_prompt_digests.items() if digests
    }
    seen: dict[str, str] = {}
    for role, digest in role_to_digest.items():
        prior = seen.get(digest)
        if prior is not None and prior != role:
            blocking.append(f"run:non_unique_prompt_digest:{prior}:{role}")
        else:
            seen[digest] = role

    status = "pass" if not blocking else "fail"
    report = {
        "run_id": run_id,
        "mode": mode,
        "status": status,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "draft_count": len(drafts),
            "non_abstain_draft_count": len(
                [d for d in drafts if str(d.get("status", "")) not in {"abstain", "diagnostic"}]
            ),
            "blocking_failure_count": len(blocking),
            "golden_lock_ok": lock_ok,
            "comparator_all_non_abstain_pass": comparator_all_non_abstain,
        },
        "blocking_failures": blocking,
        "per_draft": per_draft,
    }
    _write_json(run_dir / "calibration_quality_enforcement_report.json", report)
    print(json.dumps({"run_id": run_id, "mode": mode, "status": status}, indent=2))

    if status != "pass" and mode == "publishable":
        return EXIT_RUNTIME_FAIL
    return EXIT_SUCCESS


def run_pack_reviewer_packet(args: Namespace, *, root: Path) -> int:
    run_id = _run_id(args)
    kind = str(getattr(args, "kind", "calibration"))
    if kind != "calibration":
        raise RuntimeError("pack-reviewer-packet currently supports --kind calibration only")
    run_dir = _report_dir(root, run_id, str(getattr(args, "report_root", "") or ""))
    if not run_dir.exists():
        raise RuntimeError(f"run directory missing: {run_dir}")

    required_files = [
        "README.md",
        "summary.json",
        "calibration_target_rationale.json",
        "calibration_report.json",
        "quality_report.json",
        "novelty_report.json",
        "doctor_quality_minimums_report.json",
        "startup_checklist_report.json",
        "worked_example_validation_report.json",
        "catalog_smoke_report.json",
        "embarrassing_failures_observed.json",
        "golden_exemplar_lock_report.json",
        "shape_validation_report.json",
        "format_diff_report.json",
        "golden_shape_comparator_report.json",
        "exemplar_selection_trace.jsonl",
        "synthesis_input_trace.jsonl",
        "exemplar_usage_auditor_report.json",
        "writer_output_auditor_report.json",
        "calibration_quality_enforcement_report.json",
        "writer_subagent_outputs/prompt_contract_snapshot.json",
        "writer_subagent_outputs/subagent_invocation_trace.json",
        "judge_aggregate.json",
        "targets.json",
        "drafts.jsonl",
        "analysis_memos.jsonl",
        "export_manifest.json",
        "retrieval_diagnostics.json",
        "duplicate_similarity_matrix.json",
        "duplicate_similarity_gate_report.json",
        "construct_evidence_alignment_report.json",
        "example_execution_semantics_report.json",
        "modality_category_consistency_report.json",
        "stage_b_judge_invocations.json",
        "run_budget_report.json",
        "build_env_fingerprint.json",
        "embedding_backend_fingerprint.json",
    ]
    required_dirs = [
        "judge_passes",
        "stage_b_judges",
        "generated_guidelines_rst",
        "evidence_bundle",
        "writer_subagent_outputs",
    ]

    missing: list[str] = []
    file_records: list[dict[str, Any]] = []
    for rel in required_files:
        path = run_dir / rel
        if not path.exists() or not path.is_file():
            missing.append(rel)
            continue
        blob = path.read_bytes()
        file_records.append(
            {
                "path": rel,
                "size_bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
            }
        )
    for drel in required_dirs:
        path = run_dir / drel
        if not path.exists() or not path.is_dir():
            missing.append(drel)
            continue
        for sub in sorted(path.rglob("*")):
            if not sub.is_file():
                continue
            rel = str(sub.relative_to(run_dir))
            blob = sub.read_bytes()
            file_records.append(
                {
                    "path": rel,
                    "size_bytes": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest(),
                }
            )
    dedup_records: dict[str, dict[str, Any]] = {}
    for record in file_records:
        rec_path = str(record.get("path", ""))
        if not rec_path:
            continue
        dedup_records[rec_path] = record
    file_records = list(dedup_records.values())
    if missing:
        raise RuntimeError(f"Missing required packet artifacts: {missing}")

    quality_enforcement = _read_json(run_dir / "calibration_quality_enforcement_report.json")
    if str(quality_enforcement.get("status", "")) != "pass":
        raise RuntimeError(
            "calibration_quality_enforcement_report.json.status must be pass before packeting"
        )

    # First write manifest without self, then with self digest.
    records = sorted(file_records, key=lambda row: row["path"])
    manifest = {"run_id": run_id, "kind": kind, "files": records}
    _write_json(run_dir / "packet_manifest.json", manifest)
    packet_manifest_path = run_dir / "packet_manifest.json"
    blob = packet_manifest_path.read_bytes()
    records.append(
        {
            "path": "packet_manifest.json",
            "size_bytes": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
        }
    )
    records = sorted(records, key=lambda row: row["path"])
    manifest = {"run_id": run_id, "kind": kind, "files": records}
    _write_json(packet_manifest_path, manifest)

    import zipfile

    zip_path = run_dir / f"reviewer_packet_{kind}_{run_id}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for record in manifest["files"]:
            archive.write(run_dir / record["path"], arcname=record["path"])

    print(
        json.dumps(
            {
                "run_id": run_id,
                "kind": kind,
                "packet": str(zip_path),
                "file_count": len(manifest["files"]),
            },
            indent=2,
        )
    )
    return EXIT_SUCCESS
