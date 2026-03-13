#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class PromptSpec:
    prompt_id: str
    prompt_family: str
    query_text: str
    expected_row_markers: tuple[str, ...]
    expect_abstain: bool


def _load_testset(path: Path) -> dict[str, PromptSpec]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    prompts = payload.get("prompts", [])
    out: dict[str, PromptSpec] = {}
    for item in prompts:
        prompt_id = str(item.get("prompt_id", "")).strip()
        if not prompt_id:
            continue
        expected = tuple(
            str(x).strip().lower() for x in item.get("expected_row_markers", []) if str(x).strip()
        )
        out[prompt_id] = PromptSpec(
            prompt_id=prompt_id,
            prompt_family=str(item.get("prompt_family", "")).strip(),
            query_text=str(item.get("query_text", "")).strip(),
            expected_row_markers=expected,
            expect_abstain=bool(item.get("expect_abstain", False)),
        )
    return out


def _load_cases(eval_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(eval_path.read_text(encoding="utf-8"))
    return list(payload.get("cases", []))


def _normalize_markers(case: dict[str, Any]) -> set[str]:
    return {str(v).strip().lower() for v in case.get("projected_row_markers", []) if str(v).strip()}


def _split_for_prompt(prompt_id: str) -> str:
    value = int(hashlib.sha256(prompt_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    if value < 70:
        return "train"
    if value < 85:
        return "validation"
    return "test"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _build_dataset(
    *,
    main_eval: Path,
    holdout_eval: Path,
    main_testset: Path,
    holdout_testset: Path,
    output_dir: Path,
    candidate_id: str,
    comparator_candidate_id: str,
) -> dict[str, Any]:
    prompt_specs = {}
    prompt_specs.update(_load_testset(main_testset))
    prompt_specs.update(_load_testset(holdout_testset))

    eval_cases = _load_cases(main_eval) + _load_cases(holdout_eval)
    by_context: dict[str, dict[str, Any]] = {}
    for case in eval_cases:
        context = str(case.get("attempt_context", "")).strip()
        if context:
            by_context[context] = case

    positives: list[dict[str, Any]] = []
    hard_negatives: list[dict[str, Any]] = []
    triplets: list[dict[str, Any]] = []

    marker_positive_counts: Counter[str] = Counter()
    marker_negative_counts: Counter[str] = Counter()

    for prompt_id, spec in sorted(prompt_specs.items()):
        if spec.expect_abstain:
            continue

        lexical = by_context.get(f"{prompt_id}::lexical")
        hybrid = by_context.get(f"{prompt_id}::hybrid")
        if lexical is None or hybrid is None:
            continue

        lexical_top = [
            str(x).strip() for x in lexical.get("top_statement_ids", []) if str(x).strip()
        ]
        hybrid_top = [str(x).strip() for x in hybrid.get("top_statement_ids", []) if str(x).strip()]
        if not lexical_top or not hybrid_top:
            continue

        split = _split_for_prompt(prompt_id)
        expected_set = set(spec.expected_row_markers)
        projected_set = _normalize_markers(lexical)

        positive_uid = lexical_top[0]
        for marker in sorted(expected_set):
            marker_positive_counts.update([marker])
        positives.append(
            {
                "split": split,
                "prompt_id": prompt_id,
                "prompt_family": spec.prompt_family,
                "query_text": spec.query_text,
                "expected_row_markers": sorted(expected_set),
                "positive_chunk_uid": positive_uid,
                "source": "lexical_rank1",
                "lexical_projected_row_markers": sorted(projected_set),
            }
        )

        negative_candidates = [uid for uid in hybrid_top[:20] if uid != positive_uid]
        if not negative_candidates:
            continue

        target_negatives = min(len(negative_candidates), 4)
        selected_negatives = negative_candidates[:target_negatives]
        for negative_uid in selected_negatives:
            hard_negatives.append(
                {
                    "split": split,
                    "prompt_id": prompt_id,
                    "prompt_family": spec.prompt_family,
                    "query_text": spec.query_text,
                    "expected_row_markers": sorted(expected_set),
                    "negative_chunk_uid": negative_uid,
                    "source": "hybrid_topk_false_positive",
                }
            )
            for marker in sorted(expected_set):
                marker_negative_counts.update([marker])
            triplets.append(
                {
                    "split": split,
                    "prompt_id": prompt_id,
                    "prompt_family": spec.prompt_family,
                    "query_text": spec.query_text,
                    "expected_row_markers": sorted(expected_set),
                    "positive_chunk_uid": positive_uid,
                    "negative_chunk_uid": negative_uid,
                }
            )

    _write_jsonl(output_dir / "positives.jsonl", positives)
    _write_jsonl(output_dir / "hard_negatives.jsonl", hard_negatives)
    _write_jsonl(output_dir / "triplets.jsonl", triplets)

    by_split: dict[str, dict[str, int]] = defaultdict(
        lambda: {"positives": 0, "negatives": 0, "triplets": 0}
    )
    for row in positives:
        by_split[row["split"]]["positives"] += 1
    for row in hard_negatives:
        by_split[row["split"]]["negatives"] += 1
    for row in triplets:
        by_split[row["split"]]["triplets"] += 1

    ratio = 0.0
    if positives:
        ratio = round(len(hard_negatives) / len(positives), 4)

    summary = {
        "schema_version": 1,
        "phase": "ws5",
        "candidate_id": candidate_id,
        "comparator_candidate_id": comparator_candidate_id,
        "inputs": {
            "main_eval": str(main_eval),
            "holdout_eval": str(holdout_eval),
            "main_testset": str(main_testset),
            "holdout_testset": str(holdout_testset),
        },
        "counts": {
            "positives": len(positives),
            "hard_negatives": len(hard_negatives),
            "triplets": len(triplets),
            "negative_to_positive_ratio": ratio,
        },
        "split_counts": by_split,
        "row_marker_positive_counts": dict(sorted(marker_positive_counts.items())),
        "row_marker_negative_counts": dict(sorted(marker_negative_counts.items())),
        "notes": [
            (
                "Hard negatives are mined from available hybrid top-k false positives "
                "(up to 20 candidates, currently top-10 constrained by eval artifacts)."
            ),
            "Split assignment is deterministic by prompt_id hash with target 70/15/15.",
        ],
    }
    (output_dir / "dataset_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build WS5 weak-supervision dataset artifacts")
    parser.add_argument("--main-eval", required=True)
    parser.add_argument("--holdout-eval", required=True)
    parser.add_argument("--main-testset", required=True)
    parser.add_argument("--holdout-testset", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--comparator-candidate-id", required=True)
    args = parser.parse_args()

    summary = _build_dataset(
        main_eval=Path(args.main_eval).resolve(),
        holdout_eval=Path(args.holdout_eval).resolve(),
        main_testset=Path(args.main_testset).resolve(),
        holdout_testset=Path(args.holdout_testset).resolve(),
        output_dir=Path(args.output_dir).resolve(),
        candidate_id=str(args.candidate_id),
        comparator_candidate_id=str(args.comparator_candidate_id),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
