from __future__ import annotations

# Uses shared helpers/constants from s0_phase_a_impl during transition split.
from retrieval.services.s0_phase_a_impl import *  # noqa: F403

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

