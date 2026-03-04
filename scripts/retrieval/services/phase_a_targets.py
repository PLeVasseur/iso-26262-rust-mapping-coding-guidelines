from __future__ import annotations

# Uses shared helpers/constants from s0_phase_a_impl during transition split.
from retrieval.services.s0_phase_a_impl import *  # noqa: F403

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

