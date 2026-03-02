"""Validate Step 4 standalone judges against exemplar and known-bad RST sets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - direct script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.import_utils import GUIDELINES_REPO_ROOT
from scripts.judges_v2.stage_b import STAGE_B_JUDGES, evaluate_judge, load_judge_contracts


def _select_exemplars(manifest_path: Path, limit: int = 4) -> list[Path]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("exemplars", [])
    selected: list[Path] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rel_path = str(entry.get("path", "")).strip()
        if not rel_path:
            continue
        selected.append(GUIDELINES_REPO_ROOT / rel_path)
        if len(selected) >= limit:
            break
    return selected


def _run_exemplar_calibration(
    contracts: dict[str, Any],
    exemplar_paths: list[Path],
) -> dict[str, Any]:
    verdicts: list[dict[str, Any]] = []
    revision_cycles = {name: 0 for name in STAGE_B_JUDGES}
    for judge_name in STAGE_B_JUDGES:
        for exemplar in exemplar_paths:
            rst = exemplar.read_text(encoding="utf-8")
            result = evaluate_judge(judge_name, rst, [], contracts)
            verdicts.append(
                {
                    "judge": judge_name,
                    "exemplar": str(exemplar),
                    "verdict": result.get("decision", "fail"),
                    "reason": result.get("summary", ""),
                }
            )

    calibration_passed = all(row["verdict"] == "pass" for row in verdicts)
    return {
        "calibration_exemplars": [str(path) for path in exemplar_paths],
        "judges": STAGE_B_JUDGES,
        "total_judge_calls": len(verdicts),
        "revision_cycles": revision_cycles,
        "exemplar_verdicts": verdicts,
        "calibration_passed": calibration_passed,
    }


def _run_bad_rst_calibration(
    contracts: dict[str, Any],
    bad_rst_dir: Path,
    good_rst_dir: Path,
) -> dict[str, Any]:
    bad_by_prompt = {
        path.stem.upper().replace("-", "_"): path for path in bad_rst_dir.glob("*.rst")
    }
    good_by_prompt = {
        path.stem.upper().replace("-", "_"): path for path in good_rst_dir.glob("*.rst")
    }

    bad_rows: list[dict[str, Any]] = []
    good_rows: list[dict[str, Any]] = []
    mechanical: list[dict[str, Any]] = []
    content: list[dict[str, Any]] = []

    shared_prompts = sorted(set(bad_by_prompt).intersection(good_by_prompt))
    for prompt_id in shared_prompts:
        bad_rst = bad_by_prompt[prompt_id].read_text(encoding="utf-8")
        good_rst = good_by_prompt[prompt_id].read_text(encoding="utf-8")
        bad_verdicts: dict[str, dict[str, Any]] = {}
        good_verdicts: dict[str, dict[str, Any]] = {}
        for judge_name in STAGE_B_JUDGES:
            bad_verdicts[judge_name] = evaluate_judge(judge_name, bad_rst, [], contracts)
            good_verdicts[judge_name] = evaluate_judge(judge_name, good_rst, [], contracts)

            bad_decision = str(bad_verdicts[judge_name].get("decision", "fail"))
            good_decision = str(good_verdicts[judge_name].get("decision", "fail"))
            if bad_decision == "fail" and good_decision == "pass":
                mechanical.append(
                    {
                        "prompt_id": prompt_id,
                        "judge": judge_name,
                        "reason": "Renderer-fixed output passes where known-bad output fails.",
                    }
                )
            elif bad_decision == "fail" and good_decision == "fail":
                content.append(
                    {
                        "prompt_id": prompt_id,
                        "judge": judge_name,
                        "bad_reason_codes": bad_verdicts[judge_name].get("reason_codes", []),
                        "good_reason_codes": good_verdicts[judge_name].get("reason_codes", []),
                    }
                )

        bad_rows.append({"prompt_id": prompt_id, "verdicts": bad_verdicts})
        good_rows.append({"prompt_id": prompt_id, "verdicts": good_verdicts})

    return {
        "bad_rst": bad_rows,
        "good_rst": good_rows,
        "mechanical_failures": mechanical,
        "content_failures": content,
    }


def run_calibration(
    run_dir: Path,
    contracts_path: Path,
    exemplar_manifest_path: Path,
    known_bad_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    contracts = load_judge_contracts(contracts_path)
    exemplars = _select_exemplars(exemplar_manifest_path)
    exemplar_report = _run_exemplar_calibration(contracts, exemplars)
    bad_rst_report = _run_bad_rst_calibration(
        contracts=contracts,
        bad_rst_dir=known_bad_dir,
        good_rst_dir=run_dir / "rerendered_rst",
    )
    (run_dir / "judge_calibration_report.json").write_text(
        json.dumps(exemplar_report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "judge_calibration_bad_rst_results.json").write_text(
        json.dumps(bad_rst_report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return exemplar_report, bad_rst_report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate standalone judge calibration")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument(
        "--judge-contracts",
        default=Path("config/s0/judge_prompt_contracts.yaml"),
        type=Path,
    )
    parser.add_argument(
        "--exemplar-manifest",
        default=Path("data/exemplar_manifest.json"),
        type=Path,
    )
    parser.add_argument(
        "--known-bad-dir",
        default=Path(".cache/sqlite_kb/reports/phase_a_opencode_v3_exec2/generated_guidelines_rst"),
        type=Path,
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    exemplar_report, bad_rst_report = run_calibration(
        run_dir=args.run_dir.expanduser().resolve(),
        contracts_path=args.judge_contracts.expanduser().resolve(),
        exemplar_manifest_path=args.exemplar_manifest.expanduser().resolve(),
        known_bad_dir=args.known_bad_dir.expanduser().resolve(),
    )
    print(
        json.dumps(
            {
                "calibration_passed": exemplar_report["calibration_passed"],
                "mechanical_failures": len(bad_rst_report["mechanical_failures"]),
                "content_failures": len(bad_rst_report["content_failures"]),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
